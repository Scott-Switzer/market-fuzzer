"""ACCOUNTING_LEDGER_V1_1 — business events -> balanced journal -> derived statements.

Rules the generator can never bypass:
  * Only journal entries are posted. Statements are always *derived*; there is no plug account.
  * Every account declares its normal balance explicitly; contra accounts name what they offset.
  * Hidden/internal entries carry entity_id, event_time, and posted_at. Public availability is
    assigned by an independent publication schedule, not stored on each journal entry.
  * Period 0 is reserved for opening balances; operating periods are 1..N.
  * Period close moves revenue, expense and dividends into retained earnings via `close` entries,
    which the income statement ignores.
  * Cash flow is derived two independent ways (direct, from cash postings; indirect, from net
    income + balance deltas). QC requires exact agreement.

Scope (implemented): cash, AR, inventory, PP&E + contra accumulated depreciation, AP,
debt, common stock, retained earnings, dividends, revenue, COGS, SG&A, depreciation, interest,
income tax payable/expense, accrued interest payable, opening balances, period close.
The operational reconciliation layer for AR/AP, FIFO inventory, PP&E, and debt tranches lives in
:mod:`fwf_kernel.subledgers` and uses this ledger as its sole posting authority.
Deferred to later milestones (documented in docs/fwf/05-ledger.md): shares/EPS, treasury stock,
restatements, and multi-currency.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from .temporal import canonical_utc_instant

ZERO = Decimal(0)


class Kind(StrEnum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"


class Normal(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class CF(StrEnum):
    CASH = "cash"
    OPERATING = "operating"
    INVESTING = "investing"
    FINANCING = "financing"
    NONE = "none"  # income-statement accounts, retained earnings


@dataclass(frozen=True)
class Account:
    id: str
    kind: Kind
    normal: Normal
    xbrl: str
    cf_class: CF = CF.NONE
    contra_to: str | None = None
    noncash_expense: bool = False


CHART: dict[str, Account] = {
    a.id: a
    for a in [
        Account(
            "cash",
            Kind.ASSET,
            Normal.DEBIT,
            "us-gaap:CashAndCashEquivalentsAtCarryingValue",
            CF.CASH,
        ),
        Account(
            "ar", Kind.ASSET, Normal.DEBIT, "us-gaap:AccountsReceivableNetCurrent", CF.OPERATING
        ),
        Account("inventory", Kind.ASSET, Normal.DEBIT, "us-gaap:InventoryNet", CF.OPERATING),
        Account(
            "ppe", Kind.ASSET, Normal.DEBIT, "us-gaap:PropertyPlantAndEquipmentGross", CF.INVESTING
        ),
        Account(
            "acc_dep",
            Kind.ASSET,
            Normal.CREDIT,
            "us-gaap:AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
            CF.NONE,
            contra_to="ppe",
        ),
        Account(
            "ap", Kind.LIABILITY, Normal.CREDIT, "us-gaap:AccountsPayableCurrent", CF.OPERATING
        ),
        Account(
            "tax_payable",
            Kind.LIABILITY,
            Normal.CREDIT,
            "us-gaap:AccruedIncomeTaxesCurrent",
            CF.OPERATING,
        ),
        Account(
            "interest_payable",
            Kind.LIABILITY,
            Normal.CREDIT,
            "us-gaap:InterestPayableCurrent",
            CF.OPERATING,
        ),
        Account("debt", Kind.LIABILITY, Normal.CREDIT, "us-gaap:LongTermDebt", CF.FINANCING),
        Account(
            "common_stock", Kind.EQUITY, Normal.CREDIT, "us-gaap:CommonStockValue", CF.FINANCING
        ),
        Account(
            "additional_paid_in_capital",
            Kind.EQUITY,
            Normal.CREDIT,
            "us-gaap:AdditionalPaidInCapitalCommonStock",
            CF.FINANCING,
        ),
        Account(
            "treasury_stock",
            Kind.EQUITY,
            Normal.DEBIT,
            "us-gaap:TreasuryStockValue",
            CF.FINANCING,
            contra_to="common_stock",
        ),
        Account(
            "retained_earnings",
            Kind.EQUITY,
            Normal.CREDIT,
            "us-gaap:RetainedEarningsAccumulatedDeficit",
        ),
        Account(
            "dividends",
            Kind.EQUITY,
            Normal.DEBIT,
            "us-gaap:DividendsCommonStock",
            CF.FINANCING,
            contra_to="retained_earnings",
        ),
        Account("revenue", Kind.REVENUE, Normal.CREDIT, "us-gaap:Revenues"),
        Account("cogs", Kind.EXPENSE, Normal.DEBIT, "us-gaap:CostOfGoodsAndServicesSold"),
        Account(
            "sga", Kind.EXPENSE, Normal.DEBIT, "us-gaap:SellingGeneralAndAdministrativeExpense"
        ),
        Account(
            "depreciation", Kind.EXPENSE, Normal.DEBIT, "us-gaap:Depreciation", noncash_expense=True
        ),
        Account("interest", Kind.EXPENSE, Normal.DEBIT, "us-gaap:InterestExpense"),
        Account("tax_expense", Kind.EXPENSE, Normal.DEBIT, "us-gaap:IncomeTaxExpenseBenefit"),
    ]
}

SPECIAL_EVENTS = frozenset({"opening", "close"})


@dataclass(frozen=True)
class Line:
    account: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO

    def __post_init__(self) -> None:
        if self.account not in CHART:
            raise KeyError(f"unknown account {self.account!r}")
        if self.debit < 0 or self.credit < 0 or (self.debit and self.credit):
            raise ValueError("a line is either a non-negative debit or a non-negative credit")


@dataclass(frozen=True)
class JournalEntry:
    entry_id: str
    entity_id: str
    period: int
    event: str
    lines: tuple[Line, ...]
    event_time: str  # canonical UTC instant
    posted_at: str

    def __post_init__(self) -> None:
        if self.period < 0:
            raise ValueError(f"{self.entry_id}: period must be >= 0")
        d = sum((ln.debit for ln in self.lines), ZERO)
        c = sum((ln.credit for ln in self.lines), ZERO)
        if d != c or d == 0:
            raise ValueError(f"{self.entry_id}: debits {d} != credits {c} (or empty entry)")
        event_time = canonical_utc_instant(self.event_time)
        posted_at = canonical_utc_instant(self.posted_at)
        if event_time > posted_at:
            raise ValueError(f"{self.entry_id}: require event_time <= posted_at")
        if (self.period == 0) != (self.event == "opening"):
            raise ValueError(
                f"{self.entry_id}: period 0 is reserved for, and required by, opening entries"
            )


def signed(acct: Account, ln: Line) -> Decimal:
    """Balance in the account's own normal direction (positive = normal)."""
    return ln.debit - ln.credit if acct.normal is Normal.DEBIT else ln.credit - ln.debit


@dataclass
class Ledger:
    entity_id: str
    entries: list[JournalEntry] = field(default_factory=list)
    closed_periods: set[int] = field(default_factory=set)

    def post(self, e: JournalEntry) -> None:
        if e.period < 0:
            raise ValueError("period must be >= 0")
        if e.entity_id != self.entity_id:
            raise ValueError("entry belongs to another entity")
        if any(x.entry_id == e.entry_id for x in self.entries):
            raise ValueError(f"duplicate entry {e.entry_id}")
        if e.period in self.closed_periods:
            raise ValueError(f"period {e.period} is closed; post an adjustment in an open period")
        if e.event != "opening" and self.entries and e.period < max(x.period for x in self.entries):
            raise ValueError("backdated posting into an earlier period is not allowed")
        self.entries.append(e)

    def balances(
        self,
        through: int | None = None,
        only: int | None = None,
        exclude: frozenset[str] = frozenset(),
    ) -> dict[str, Decimal]:
        out = {a: ZERO for a in CHART}
        for e in self.entries:
            if (through is not None and e.period > through) or (
                only is not None and e.period != only
            ):
                continue
            if e.event in exclude:
                continue
            for ln in e.lines:
                out[ln.account] += signed(CHART[ln.account], ln)
        return out

    def close(self, period: int, ts: str) -> None:
        """Close revenue, expense and dividends into retained earnings."""
        if period < 0:
            raise ValueError("period must be >= 0")
        b = self.balances(only=period, exclude=frozenset({"close"}))
        dr: dict[str, Decimal] = {}
        cr: dict[str, Decimal] = {}
        for a, v in b.items():
            acct = CHART[a]
            if acct.kind not in (Kind.REVENUE, Kind.EXPENSE) and a != "dividends":
                continue
            if v == 0:
                continue
            # reverse the account's normal-direction balance
            side = cr if acct.normal is Normal.DEBIT else dr
            if v < 0:
                side = dr if side is cr else cr
            side[a] = side.get(a, ZERO) + abs(v)
        net = sum(dr.values(), ZERO) - sum(cr.values(), ZERO)
        if net > 0:
            cr["retained_earnings"] = net
        elif net < 0:
            dr["retained_earnings"] = -net
        if dr or cr:
            self.post(make(f"close-{period}", self.entity_id, period, "close", dr, cr, ts, ts))
        self.closed_periods.add(period)


def make(
    entry_id: str,
    entity_id: str,
    period: int,
    event: str,
    dr: dict,
    cr: dict,
    event_time: str,
    posted_at: str,
) -> JournalEntry:
    lines = [Line(a, debit=Decimal(v)) for a, v in dr.items()]
    lines += [Line(a, credit=Decimal(v)) for a, v in cr.items()]
    return JournalEntry(entry_id, entity_id, period, event, tuple(lines), event_time, posted_at)


# Canonical business events: (debits, credits) templates. The generator speaks only in these.
EVENTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "sale_on_credit": (("ar",), ("revenue",)),
    "collect_ar": (("cash",), ("ar",)),
    "buy_inventory_on_credit": (("inventory",), ("ap",)),
    "ship_goods": (("cogs",), ("inventory",)),
    "pay_ap": (("ap",), ("cash",)),
    "pay_sga": (("sga",), ("cash",)),
    "capex": (("ppe",), ("cash",)),
    "depreciate": (("depreciation",), ("acc_dep",)),
    "borrow": (("cash",), ("debt",)),
    "repay": (("debt",), ("cash",)),
    "pay_interest": (("interest",), ("cash",)),
    "accrue_interest": (("interest",), ("interest_payable",)),
    "pay_accrued_interest": (("interest_payable",), ("cash",)),
    "accrue_tax": (("tax_expense",), ("tax_payable",)),
    "pay_tax": (("tax_payable",), ("cash",)),
    "issue_equity": (("cash",), ("common_stock",)),
    "issue_common_shares": (("cash",), ("common_stock", "additional_paid_in_capital")),
    "repurchase_common_shares": (("treasury_stock",), ("cash",)),
    "pay_dividend": (("dividends",), ("cash",)),
}


def event(
    entry_id: str,
    entity_id: str,
    period: int,
    name: str,
    amount: str | Decimal | Mapping[str, str | Decimal],
    event_time: str,
    posted_at: str,
) -> JournalEntry:
    debits, credits = EVENTS[name]
    if isinstance(amount, Mapping):
        expected_accounts = set(debits) | set(credits)
        if set(amount) != expected_accounts:
            raise ValueError(f"{name} requires exactly accounts {sorted(expected_accounts)}")
        debit_amounts = {account: amount[account] for account in debits}
        credit_amounts = {account: amount[account] for account in credits}
    else:
        if len(debits) != 1 or len(credits) != 1:
            raise ValueError(f"{name} requires a per-account amount mapping")
        debit_amounts = {debits[0]: amount}
        credit_amounts = {credits[0]: amount}
    return make(
        entry_id,
        entity_id,
        period,
        name,
        debit_amounts,
        credit_amounts,
        event_time,
        posted_at,
    )


# ---------------- derived statements ----------------
_NON_OPERATING = frozenset({"opening", "close"})


def income_statement(led: Ledger, period: int) -> dict[str, Decimal]:
    b = led.balances(only=period, exclude=_NON_OPERATING)
    rev = b["revenue"]
    gp = rev - b["cogs"]
    op = gp - b["sga"] - b["depreciation"]
    pretax = op - b["interest"]
    return {
        "revenue": rev,
        "cogs": b["cogs"],
        "gross_profit": gp,
        "sga": b["sga"],
        "depreciation": b["depreciation"],
        "operating_income": op,
        "interest": b["interest"],
        "pretax_income": pretax,
        "tax_expense": b["tax_expense"],
        "net_income": pretax - b["tax_expense"],
    }


def balance_sheet(led: Ledger, period: int) -> dict[str, Decimal]:
    b = led.balances(through=period)
    unclosed_ni = sum(
        (b[a] if CHART[a].kind is Kind.REVENUE else -b[a])
        for a in CHART
        if CHART[a].kind in (Kind.REVENUE, Kind.EXPENSE)
    )
    ppe_net = b["ppe"] - b["acc_dep"]
    assets = b["cash"] + b["ar"] + b["inventory"] + ppe_net
    liabilities = b["ap"] + b["tax_payable"] + b["interest_payable"] + b["debt"]
    equity = (
        b["common_stock"]
        + b["additional_paid_in_capital"]
        + b["retained_earnings"]
        + unclosed_ni
        - b["dividends"]
        - b["treasury_stock"]
    )
    keys = (
        "cash",
        "ar",
        "inventory",
        "ppe",
        "acc_dep",
        "ap",
        "tax_payable",
        "interest_payable",
        "debt",
        "common_stock",
        "additional_paid_in_capital",
        "treasury_stock",
        "retained_earnings",
    )
    return {
        **{k: b[k] for k in keys},
        "ppe_net": ppe_net,
        "total_assets": assets,
        "total_liabilities": liabilities,
        "total_equity": equity,
    }


def cash_flow_direct(led: Ledger, period: int) -> dict[str, Decimal]:
    """Classify each cash posting by its counterparty account's cf_class."""
    out = {CF.OPERATING: ZERO, CF.INVESTING: ZERO, CF.FINANCING: ZERO}
    for e in led.entries:
        if e.period != period or e.event in _NON_OPERATING:
            continue
        cash = sum((signed(CHART["cash"], ln) for ln in e.lines if ln.account == "cash"), ZERO)
        if not cash:
            continue
        classes = {CHART[ln.account].cf_class for ln in e.lines if ln.account != "cash"}
        cls = (
            CF.INVESTING
            if CF.INVESTING in classes
            else CF.FINANCING
            if CF.FINANCING in classes
            else CF.OPERATING
        )
        out[cls] += cash
    return {k.value: v for k, v in out.items()}


def cash_flow_indirect(led: Ledger, period: int) -> dict[str, Decimal]:
    """NI + non-cash add-backs - increase in operating assets + increase in operating liabilities."""
    cur, base = led.balances(through=period), led.balances(through=period - 1)
    d = {a: cur[a] - base[a] for a in CHART}
    is_ = income_statement(led, period)
    addbacks = sum((is_[a] for a in ("depreciation",) if CHART[a].noncash_expense), ZERO)
    wc = sum(
        (-d[a] if CHART[a].kind is Kind.ASSET else d[a])
        for a in CHART
        if CHART[a].cf_class is CF.OPERATING
    )
    return {
        "operating": is_["net_income"] + addbacks + wc,
        "investing": -d["ppe"],
        "financing": (
            d["debt"]
            + d["common_stock"]
            + d["additional_paid_in_capital"]
            - d["treasury_stock"]
            - is_dividends(led, period)
        ),
    }


def is_dividends(led: Ledger, period: int) -> Decimal:
    return led.balances(only=period, exclude=_NON_OPERATING)["dividends"]
