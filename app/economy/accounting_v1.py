"""Market Fuzzer accounting adapter around the pinned FWF kernel.

The causal economy supplies monetary targets; this module turns them into balanced journal
activity and exposes only derived statements and immutable filing commitments.  It never
manufactures a balancing amount or stores an unquantized float as accounting truth.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, DecimalException
from typing import Any, TypedDict

from app._vendor.fwf_kernel import equity as E
from app._vendor.fwf_kernel import filings as F
from app._vendor.fwf_kernel import ledger as L
from app._vendor.fwf_kernel import subledgers as S

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
PAR_VALUE = Decimal("0.01")
DEFAULT_USEFUL_LIFE_QUARTERS = 80
FRAUD_REVENUE_RATE = Decimal("0.004")
QUARTER_DAYS = Decimal("90")
FILING_HOUR = 21
TAX_EVENT = "accrue_tax"
DIVIDEND_EVENT = "pay_dividend"

MoneySource = str | int | Decimal | float


def exact_money(value: MoneySource, field_name: str = "amount") -> Decimal:
    """Convert an economic value through ``Decimal(str(value))`` and cents."""
    if isinstance(value, bool):
        raise TypeError(f"{field_name} cannot be boolean")
    try:
        amount = Decimal(str(value))
    except (DecimalException, ValueError) as exc:
        raise ValueError(f"invalid monetary value for {field_name}: {value!r}") from exc
    if not amount.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if amount < 0:
        raise ValueError(f"{field_name} must be non-negative")
    try:
        result = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    except DecimalException as exc:
        raise ValueError(f"{field_name} cannot be represented at cent precision") from exc
    return ZERO if result == 0 else result


def positive_money(value: MoneySource, field_name: str = "amount") -> Decimal:
    amount = exact_money(value, field_name)
    if amount <= ZERO:
        raise ValueError(f"{field_name} must be positive")
    return amount


def decimal_text(value: Decimal) -> str:
    """Render an exact Decimal without binary-float or exponent ambiguity."""
    if value == 0:
        value = ZERO
    return format(value, "f")


def exact_rate(value: MoneySource, field_name: str = "rate") -> Decimal:
    if isinstance(value, bool):
        raise TypeError(f"{field_name} cannot be boolean")
    try:
        rate = Decimal(str(value))
    except (DecimalException, ValueError) as exc:
        raise ValueError(f"invalid rate: {value!r}") from exc
    if not rate.is_finite() or rate < 0:
        raise ValueError(f"{field_name} must be finite and non-negative")
    return rate


def useful_life_quarters(depreciation_rate: MoneySource) -> int:
    rate = exact_rate(depreciation_rate, "depreciation_rate")
    if rate <= 0:
        raise ValueError("depreciation_rate must be positive")
    life = int((Decimal("4") / rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return max(1, life)


def quarter_bounds(period_end: date) -> tuple[date, date]:
    if period_end.month == 3:
        start = date(period_end.year, 1, 1)
    elif period_end.month == 6:
        start = date(period_end.year, 4, 1)
    elif period_end.month == 9:
        start = date(period_end.year, 7, 1)
    elif period_end.month == 12:
        start = date(period_end.year, 10, 1)
    else:
        raise ValueError("period_end must be a calendar quarter end")
    return start, period_end


def accounting_instant(period_end: date, slot: int) -> tuple[str, str]:
    """Return deterministic event/posted instants on the quarter-end UTC date."""
    if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot <= 86_398:
        raise ValueError("accounting slot must be in [0, 86398]")
    base = datetime(period_end.year, period_end.month, period_end.day, tzinfo=UTC)
    event = base + timedelta(seconds=slot)
    posted = event + timedelta(seconds=1)
    return _instant_text(event), _instant_text(posted)


def close_instant(period_end: date) -> str:
    base = datetime(period_end.year, period_end.month, period_end.day, 23, 59, 59, tzinfo=UTC)
    return _instant_text(base)


def filing_instant(available_date: date) -> str:
    return f"{available_date.isoformat()}T{FILING_HOUR:02d}:00:00Z"


def _instant_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _period_label(period_end: date) -> str:
    return f"{period_end.year}Q{((period_end.month - 1) // 3) + 1}"


def _period_id(ticker: str, period_end: date) -> str:
    return f"{ticker}:{_period_label(period_end)}"


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class OpeningStateV1(TypedDict):
    cash: Decimal
    ar: Decimal
    inventory: Decimal
    ppe: Decimal
    ap: Decimal
    debt: Decimal
    common_stock: Decimal
    additional_paid_in_capital: Decimal
    retained_earnings: Decimal
    treasury_stock: Decimal
    issued_shares: int


@dataclass(frozen=True)
class FilingArtifactV1:
    filing_id: str
    version_id: str
    version: int
    form: F.FilingKind
    period: int
    period_end: date
    available_at: str
    payload: dict[str, Any]
    payload_sha256: str


@dataclass(frozen=True)
class AccountingQuarterV1:
    company: str
    sector: str
    period: int
    period_start: date
    period_end: date
    available_date: date
    available_at: str
    income_statement: dict[str, Decimal]
    balance_sheet: dict[str, Decimal]
    cash_flow: dict[str, Decimal]
    direct_cash_flow: dict[str, Decimal]
    indirect_cash_flow: dict[str, Decimal]
    weighted_average_shares: Decimal
    basic_eps: Decimal
    opening_cash: Decimal
    closing_cash: Decimal
    net_change_in_cash: Decimal
    capex: Decimal
    dividends: Decimal
    filing: FilingArtifactV1
    fraud_flag: bool


class CompanyAccountingV1:
    """Own one company's pinned-kernel books and deterministic business transactions."""

    def __init__(
        self,
        *,
        ticker: str,
        initial_shares: int,
        cash_target: MoneySource,
        ar_target: MoneySource,
        inventory_target: MoneySource,
        ppe_target: MoneySource,
        ap_target: MoneySource,
        debt_target: MoneySource,
        retained_target: MoneySource,
        depreciation_rate: MoneySource,
        interest_rate: MoneySource,
        tax_rate: MoneySource,
        payout_ratio: MoneySource,
        min_cash_buffer_ratio: MoneySource,
        sector: str,
        horizon_quarters: int,
        opening_date: date | None = None,
    ) -> None:
        if not isinstance(ticker, str) or not ticker.strip():
            raise ValueError("ticker must be non-empty")
        if not isinstance(sector, str) or not sector.strip():
            raise ValueError("sector must be non-empty")
        if isinstance(initial_shares, bool) or not isinstance(initial_shares, int) or initial_shares <= 0:
            raise ValueError("initial_shares must be a positive integer")
        if (
            isinstance(horizon_quarters, bool)
            or not isinstance(horizon_quarters, int)
            or horizon_quarters < 1
        ):
            raise ValueError("horizon_quarters must be positive")
        self.ticker = ticker
        self.sector = sector
        self.entity_id = f"COMPANY:{ticker}"
        self.period_horizon = horizon_quarters
        self.opening_date = opening_date or date(2025, 1, 1)
        if not isinstance(self.opening_date, date) or isinstance(self.opening_date, datetime):
            raise ValueError("opening_date must be a date")
        self.depreciation_rate = exact_rate(depreciation_rate, "depreciation_rate")
        self.interest_rate = exact_rate(interest_rate, "interest_rate")
        self.tax_rate = exact_rate(tax_rate, "tax_rate")
        self.payout_ratio = exact_rate(payout_ratio, "payout_ratio")
        self.min_cash_buffer_ratio = exact_rate(min_cash_buffer_ratio, "min_cash_buffer_ratio")
        if self.tax_rate > 1:
            raise ValueError("tax_rate must be in [0, 1]")
        if self.payout_ratio > 1:
            raise ValueError("payout_ratio must be in [0, 1]")
        self.useful_life_periods = useful_life_quarters(self.depreciation_rate)

        cash = positive_money(cash_target, "opening cash")
        ar = positive_money(ar_target, "opening accounts receivable")
        inventory = positive_money(inventory_target, "opening inventory")
        ppe = positive_money(ppe_target, "opening PP&E")
        requested_ap = positive_money(ap_target, "opening accounts payable target")
        requested_debt = exact_money(debt_target, "opening debt target")
        requested_retained = exact_money(retained_target, "opening retained earnings target")
        opening_assets = cash + ar + inventory + ppe
        common_stock = exact_money(Decimal(initial_shares) * PAR_VALUE, "common stock")

        # Economic targets are translated into a solvent opening state.  If a
        # bounded draw asks for liabilities that exceed assets, reduce debt
        # first and then AP by the smallest cent-exact amount.  This is a
        # liability-target constraint, not an equity or cash balancing amount.
        liability_capacity = opening_assets - common_stock
        if liability_capacity < ZERO:
            raise ValueError("opening assets do not cover par common stock")
        ap = min(requested_ap, liability_capacity)
        debt = min(requested_debt, liability_capacity - ap)
        opening_liabilities = ap + debt
        opening_equity = opening_assets - opening_liabilities
        remaining = opening_equity - common_stock
        retained = min(max(ZERO, requested_retained), remaining)
        apic = remaining - retained
        if apic < ZERO:
            raise ValueError("opening APIC cannot be negative")

        self.opening_targets: dict[str, Decimal] = {
            "cash": cash,
            "ar": ar,
            "inventory": inventory,
            "ppe": ppe,
            "ap": requested_ap,
            "debt": requested_debt,
            "retained_earnings": requested_retained,
        }
        self.opening_state: OpeningStateV1 = {
            "cash": cash,
            "ar": ar,
            "inventory": inventory,
            "ppe": ppe,
            "ap": ap,
            "debt": debt,
            "common_stock": common_stock,
            "additional_paid_in_capital": apic,
            "retained_earnings": retained,
            "treasury_stock": ZERO,
            "issued_shares": initial_shares,
        }
        self.ledger = L.Ledger(self.entity_id)
        opening_time = f"{self.opening_date.isoformat()}T00:00:00Z"
        opening_debits = {"cash": cash, "ar": ar, "inventory": inventory, "ppe": ppe}
        opening_credits = {
            "ap": ap,
            "debt": debt,
            "common_stock": common_stock,
            "additional_paid_in_capital": apic,
            "retained_earnings": retained,
        }
        self.ledger.post(
            L.make(
                f"opening:{ticker}",
                self.entity_id,
                0,
                "opening",
                {account: amount for account, amount in opening_debits.items() if amount > ZERO},
                {account: amount for account, amount in opening_credits.items() if amount > ZERO},
                opening_time,
                opening_time,
            )
        )
        self.ledger.close(0, opening_time)

        maturity = horizon_quarters + 1
        self.operational = S.OperationalBook(
            self.ledger,
            receivables=(S.ReceivableInvoice(f"ar:{ticker}:opening", self.entity_id, 0, 0, ar, ar),),
            payables=(
                (S.PayableInvoice(f"ap:{ticker}:opening", self.entity_id, 0, 0, ap, ap),) if ap > ZERO else ()
            ),
            inventory_layers=(S.InventoryLayer(f"inventory:{ticker}:opening", 0, inventory, inventory),),
            ppe_assets=(
                S.PPEAsset(
                    f"ppe:{ticker}:opening",
                    self.entity_id,
                    0,
                    ppe,
                    ZERO,
                    self.useful_life_periods,
                    ZERO,
                ),
            ),
            debt_tranches=(
                (
                    S.DebtTranche(
                        f"debt:{ticker}:opening",
                        self.entity_id,
                        0,
                        maturity,
                        debt,
                        debt,
                        self.interest_rate,
                    ),
                )
                if debt > ZERO
                else ()
            ),
        )
        self.equity = E.EquityBook(
            self.ledger,
            E.CommonShareClass(f"common:{ticker}", PAR_VALUE),
            opening_state=E.OpeningEquityState(
                initial_shares,
                0,
                common_stock,
                apic,
                ZERO,
            ),
        )
        self.filings = F.FilingBook()
        self._payloads: dict[str, FilingArtifactV1] = {}
        self._period_results: dict[int, AccountingQuarterV1] = {}
        self._clock_slots: dict[int, int] = {}
        self._entry_sequence = 0
        self._liquidity_sequence: dict[int, int] = {}
        self._last_period_end: date | None = None
        self._require_valid()

    @property
    def issued_shares(self) -> int:
        return self.equity.issued_shares

    @property
    def filing_artifacts(self) -> tuple[FilingArtifactV1, ...]:
        return tuple(self._payloads.values())

    @property
    def period_results(self) -> tuple[AccountingQuarterV1, ...]:
        return tuple(self._period_results.values())

    def _require_valid(self) -> None:
        if self.operational.validate_reconciliations():
            raise ValueError("invalid operational accounting state")
        if self.equity.validate_reconciliations():
            raise ValueError("invalid equity accounting state")
        if self.filings.validate_reconciliations():
            raise ValueError("invalid filing accounting state")

    def _next_instant(self, period_end: date, period: int) -> tuple[str, str]:
        slot = self._clock_slots.get(period, 0)
        instant_slot = 3_600 + slot * 30
        if instant_slot > 86_398:
            raise ValueError("too many accounting operations in one period")
        self._clock_slots[period] = slot + 1
        return accounting_instant(period_end, instant_slot)

    def _next_entry_id(self, period: int, event: str) -> str:
        self._entry_sequence += 1
        return f"m43:{self.ticker}:{period:04d}:{self._entry_sequence:08d}:{event}"

    def _post_ledger_event(
        self,
        event: str,
        period: int,
        amount: Decimal,
        period_end: date,
    ) -> None:
        if amount <= ZERO:
            return
        event_time, posted_at = self._next_instant(period_end, period)
        self.ledger.post(
            L.event(
                self._next_entry_id(period, event),
                self.entity_id,
                period,
                event,
                amount,
                event_time,
                posted_at,
            )
        )

    def _cash(self) -> Decimal:
        return self.ledger.balances()["cash"]

    def _buffer(self, revenue: Decimal) -> Decimal:
        return exact_money(revenue * self.min_cash_buffer_ratio, "minimum cash buffer")

    def _ensure_cash(
        self,
        payment: Decimal,
        revenue: Decimal,
        period: int,
        period_end: date,
        current_rate: Decimal,
    ) -> None:
        if payment <= ZERO:
            return
        required = payment + self._buffer(revenue)
        shortfall = required - self._cash()
        if shortfall <= ZERO:
            return
        amount = positive_money(shortfall, "liquidity shortfall")
        sequence = self._liquidity_sequence.get(period, 0) + 1
        self._liquidity_sequence[period] = sequence
        event_time, posted_at = self._next_instant(period_end, period)
        self.operational.borrow_debt(
            f"liquidity:{self.ticker}:{period}:{sequence}",
            self.entity_id,
            period,
            self.period_horizon + 1,
            amount,
            current_rate,
            event_time,
            posted_at,
        )

    def _collect_receivables(
        self,
        period: int,
        period_end: date,
        desired_genuine_ar: Decimal,
    ) -> None:
        genuine = [
            invoice
            for invoice_id, invoice in self.operational.receivables.items()
            if not invoice_id.startswith("fraud:") and invoice.outstanding_amount > ZERO
        ]
        genuine_total = sum((invoice.outstanding_amount for invoice in genuine), ZERO)
        to_collect = max(ZERO, genuine_total - desired_genuine_ar)
        for invoice in sorted(genuine, key=lambda item: (item.issued_period, item.invoice_id)):
            if to_collect <= ZERO:
                break
            amount = min(invoice.outstanding_amount, to_collect)
            if amount <= ZERO:
                continue
            event_time, posted_at = self._next_instant(period_end, period)
            self.operational.collect_receivable(invoice.invoice_id, amount, period, event_time, posted_at)
            to_collect -= amount

    def _pay_payables(
        self,
        period: int,
        period_end: date,
        revenue: Decimal,
        desired_ap: Decimal,
        current_rate: Decimal,
    ) -> None:
        current_ap = self.ledger.balances()["ap"]
        to_pay = max(ZERO, current_ap - desired_ap)
        if to_pay <= ZERO:
            return
        self._ensure_cash(to_pay, revenue, period, period_end, current_rate)
        invoices = sorted(
            (invoice for invoice in self.operational.payables.values() if invoice.outstanding_amount > ZERO),
            key=lambda item: (item.issued_period, item.invoice_id),
        )
        remaining = to_pay
        for invoice in invoices:
            if remaining <= ZERO:
                break
            amount = min(invoice.outstanding_amount, remaining)
            if amount <= ZERO:
                continue
            event_time, posted_at = self._next_instant(period_end, period)
            self.operational.pay_payable(invoice.invoice_id, amount, period, event_time, posted_at)
            remaining -= amount

    def _accrue_and_pay_interest(
        self,
        period: int,
        period_end: date,
        revenue: Decimal,
        current_rate: Decimal,
    ) -> None:
        tranches = sorted(self.operational.debt_tranches.values(), key=lambda item: item.tranche_id)
        for tranche in tranches:
            if tranche.issued_period < period <= tranche.maturity_period:
                event_time, posted_at = self._next_instant(period_end, period)
                self.operational.accrue_interest(
                    tranche.tranche_id,
                    period,
                    event_time,
                    posted_at,
                )
        unpaid = sum(
            (schedule.unpaid_amount for schedule in self.operational.accrued_interest.values()),
            ZERO,
        )
        if unpaid <= ZERO:
            return
        self._ensure_cash(unpaid, revenue, period, period_end, current_rate)
        event_time, posted_at = self._next_instant(period_end, period)
        self.operational.pay_accrued_interest(unpaid, period, event_time, posted_at)

    def _optional_repayment(
        self,
        period: int,
        period_end: date,
        revenue: Decimal,
    ) -> None:
        excess = max(ZERO, self._cash() - self._buffer(revenue))
        tranches = sorted(
            (
                tranche
                for tranche in self.operational.debt_tranches.values()
                if tranche.issued_period < period and tranche.principal_outstanding > ZERO
            ),
            key=lambda item: (item.issued_period, item.tranche_id),
        )
        for tranche in tranches:
            if excess <= ZERO:
                break
            amount = min(excess, tranche.principal_outstanding)
            if amount <= ZERO:
                continue
            event_time, posted_at = self._next_instant(period_end, period)
            self.operational.repay_debt(tranche.tranche_id, amount, period, event_time, posted_at)
            excess -= amount

    def _build_payload(
        self,
        *,
        period: int,
        period_start: date,
        period_end: date,
        income: dict[str, Decimal],
        balance: dict[str, Decimal],
        cash_flow: dict[str, Decimal],
        waso: Decimal,
        eps: Decimal,
    ) -> dict[str, Any]:
        return {
            "schema": "fwf-statement-payload-v1",
            "entity_id": self.entity_id,
            "period": period,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "income_statement": {key: decimal_text(value) for key, value in sorted(income.items())},
            "balance_sheet": {key: decimal_text(value) for key, value in sorted(balance.items())},
            "cash_flow": {key: decimal_text(value) for key, value in sorted(cash_flow.items())},
            "weighted_average_shares": decimal_text(waso),
            "basic_eps": decimal_text(eps),
        }

    def apply_quarter(
        self,
        *,
        sector: str,
        period: int,
        period_end: date,
        true_revenue: MoneySource,
        gross_margin: MoneySource,
        days_receivable: MoneySource,
        days_inventory: MoneySource,
        days_payable: MoneySource,
        sga: MoneySource,
        capex: MoneySource,
        tax_rate: MoneySource,
        payout_ratio: MoneySource,
        current_interest_rate: MoneySource,
        fraud: bool,
    ) -> AccountingQuarterV1:
        """Journal one quarter and return only ledger-derived statements and filing data."""
        if isinstance(period, bool) or not isinstance(period, int) or period < 1:
            raise ValueError("accounting period must be >= 1")
        expected_period = max(self._period_results, default=0) + 1
        if period != expected_period:
            raise ValueError(f"accounting periods must be consecutive; expected {expected_period}")
        if period > self.period_horizon:
            raise ValueError("accounting period exceeds the company horizon")
        if self._last_period_end is not None and period_end <= self._last_period_end:
            raise ValueError("period_end must advance chronologically")
        period_start, calculated_end = quarter_bounds(period_end)
        if calculated_end != period_end:
            raise ValueError("period_end is not a canonical quarter end")
        revenue = positive_money(true_revenue, "true revenue")
        modeled_margin = exact_rate(gross_margin, "gross margin")
        if modeled_margin < 0 or modeled_margin > 1:
            raise ValueError("gross margin must be in [0, 1]")
        ar_days = exact_rate(days_receivable, "days receivable")
        inventory_days = exact_rate(days_inventory, "days inventory")
        payable_days = exact_rate(days_payable, "days payable")
        opex = exact_money(sga, "SG&A")
        capex_amount = exact_money(capex, "capex")
        rate = exact_rate(current_interest_rate, "current interest rate")
        tax_rate_value = exact_rate(tax_rate, "tax rate")
        payout_value = exact_rate(payout_ratio, "payout ratio")
        if tax_rate_value > 1:
            raise ValueError("tax rate must be in [0, 1]")
        if payout_value > 1:
            raise ValueError("payout ratio must be in [0, 1]")
        if not isinstance(fraud, bool):
            raise TypeError("fraud must be boolean")

        genuine_invoice_id = f"ar:{self.ticker}:{period}:genuine"
        event_time, posted_at = self._next_instant(period_end, period)
        self.operational.issue_receivable(
            genuine_invoice_id,
            self.entity_id,
            period,
            period + 1,
            revenue,
            event_time,
            posted_at,
        )
        if fraud:
            fraud_amount = exact_money(revenue * FRAUD_REVENUE_RATE, "fraud invoice")
            if fraud_amount > ZERO:
                event_time, posted_at = self._next_instant(period_end, period)
                self.operational.issue_receivable(
                    f"fraud:{self.ticker}:{period}",
                    self.entity_id,
                    period,
                    period + 1,
                    fraud_amount,
                    event_time,
                    posted_at,
                )

        desired_ar = exact_money(revenue * ar_days / QUARTER_DAYS, "desired AR")
        self._collect_receivables(period, period_end, desired_ar)

        desired_cogs = exact_money(revenue * (Decimal("1") - modeled_margin), "desired COGS")
        desired_inventory = exact_money(revenue * inventory_days / QUARTER_DAYS, "desired inventory")
        current_inventory = self.ledger.balances()["inventory"]
        purchase_amount = max(ZERO, desired_cogs + desired_inventory - current_inventory)
        if purchase_amount > ZERO:
            event_time, posted_at = self._next_instant(period_end, period)
            self.operational.issue_inventory_payable(
                f"ap:{self.ticker}:{period}:inventory",
                f"inventory:{self.ticker}:{period}",
                self.entity_id,
                period,
                period + 1,
                purchase_amount,
                event_time,
                posted_at,
            )
        if desired_cogs > ZERO:
            event_time, posted_at = self._next_instant(period_end, period)
            self.operational.consume_inventory(desired_cogs, period, event_time, posted_at)

        desired_ap = exact_money(revenue * payable_days / QUARTER_DAYS, "desired AP")
        self._pay_payables(period, period_end, revenue, desired_ap, rate)

        if opex > ZERO:
            self._ensure_cash(opex, revenue, period, period_end, rate)
            self._post_ledger_event("pay_sga", period, opex, period_end)

        if capex_amount > ZERO:
            self._ensure_cash(capex_amount, revenue, period, period_end, rate)
            event_time, posted_at = self._next_instant(period_end, period)
            self.operational.acquire_ppe_asset(
                f"ppe:{self.ticker}:{period}",
                self.entity_id,
                period,
                capex_amount,
                ZERO,
                self.useful_life_periods,
                event_time,
                posted_at,
            )
        event_time, posted_at = self._next_instant(period_end, period)
        self.operational.depreciate_ppe(period, event_time, posted_at)

        self._accrue_and_pay_interest(period, period_end, revenue, rate)

        pre_tax = L.income_statement(self.ledger, period)
        pretax_income = pre_tax["pretax_income"]
        tax_amount = max(ZERO, exact_money(max(ZERO, pretax_income) * tax_rate_value, "tax expense"))
        if tax_amount > ZERO:
            self._post_ledger_event(TAX_EVENT, period, tax_amount, period_end)
            self._ensure_cash(tax_amount, revenue, period, period_end, rate)
            self._post_ledger_event("pay_tax", period, tax_amount, period_end)

        income_after_tax = L.income_statement(self.ledger, period)
        desired_dividend = max(
            ZERO,
            exact_money(max(ZERO, income_after_tax["net_income"]) * payout_value, "dividend"),
        )
        available_for_dividend = max(ZERO, self._cash() - self._buffer(revenue))
        dividend = min(desired_dividend, available_for_dividend)
        if dividend > ZERO:
            self._post_ledger_event(DIVIDEND_EVENT, period, dividend, period_end)

        self._optional_repayment(period, period_end, revenue)

        pre_income = L.income_statement(self.ledger, period)
        pre_balance = L.balance_sheet(self.ledger, period)
        pre_direct = L.cash_flow_direct(self.ledger, period)
        pre_indirect = L.cash_flow_indirect(self.ledger, period)
        if pre_direct != pre_indirect:
            raise ValueError("direct and indirect cash flow do not reconcile")
        if pre_balance["total_assets"] != pre_balance["total_liabilities"] + pre_balance["total_equity"]:
            raise ValueError("balance sheet does not reconcile before close")
        opening_cash = self.ledger.balances(through=period - 1)["cash"]
        closing_cash = self.ledger.balances(through=period)["cash"]
        if opening_cash + sum(pre_direct.values(), ZERO) != closing_cash:
            raise ValueError("cash roll does not reconcile")
        self._require_valid()

        opening_gross_ppe = self.ledger.balances(through=period - 1)["ppe"]
        close_timestamp = close_instant(period_end)
        if any(entry.posted_at > close_timestamp for entry in self.ledger.entries if entry.period == period):
            raise ValueError("an accounting transaction was posted after period close")
        self.ledger.close(period, close_timestamp)
        post_income = L.income_statement(self.ledger, period)
        post_balance = L.balance_sheet(self.ledger, period)
        post_direct = L.cash_flow_direct(self.ledger, period)
        post_indirect = L.cash_flow_indirect(self.ledger, period)
        balance_keys = (
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
            "ppe_net",
            "total_assets",
            "total_liabilities",
            "total_equity",
        )
        if (
            post_income != pre_income
            or any(post_balance[key] != pre_balance[key] for key in balance_keys)
            or post_direct != pre_direct
            or post_indirect != pre_indirect
        ):
            raise ValueError("period close changed derived statement values")
        if post_direct != post_indirect:
            raise ValueError("direct and indirect cash flow diverged after close")
        if post_balance["total_assets"] != post_balance["total_liabilities"] + post_balance["total_equity"]:
            raise ValueError("balance sheet does not reconcile after close")
        self._require_valid()

        capex = post_balance["ppe"] - opening_gross_ppe
        dividends = L.is_dividends(self.ledger, period)
        waso = self.equity.weighted_average_shares(
            period_start,
            period_end,
            as_of_date=period_end,
        )
        eps = self.equity.basic_eps(
            post_income["net_income"],
            period_start,
            period_end,
            as_of_date=period_end,
        )
        available_date = period_end + timedelta(days=60 if period_end.month == 12 else 35)
        payload = self._build_payload(
            period=period,
            period_start=period_start,
            period_end=period_end,
            income=post_income,
            balance=post_balance,
            cash_flow=post_direct,
            waso=waso,
            eps=eps,
        )
        payload_hash = canonical_payload_hash(payload)
        filing_id = _period_id(self.ticker, period_end)
        version_id = f"{filing_id}:v1"
        filing_version = self.filings.publish_original(
            filing_id,
            version_id,
            self.entity_id,
            F.FilingKind.TEN_K if period_end.month == 12 else F.FilingKind.TEN_Q,
            period,
            period_end,
            payload_hash,
            filing_instant(available_date),
        )
        artifact = FilingArtifactV1(
            filing_id=filing_id,
            version_id=version_id,
            version=filing_version.version,
            form=filing_version.form,
            period=period,
            period_end=period_end,
            available_at=filing_version.available_at,
            payload=payload,
            payload_sha256=payload_hash,
        )
        self._payloads[filing_id] = artifact
        net_change_in_cash = sum(post_direct.values(), ZERO)
        result = AccountingQuarterV1(
            company=self.ticker,
            sector=sector,
            period=period,
            period_start=period_start,
            period_end=period_end,
            available_date=available_date,
            available_at=artifact.available_at,
            income_statement=post_income,
            balance_sheet=post_balance,
            cash_flow=post_direct,
            direct_cash_flow=post_direct,
            indirect_cash_flow=post_indirect,
            weighted_average_shares=waso,
            basic_eps=eps,
            opening_cash=opening_cash,
            closing_cash=post_balance["cash"],
            net_change_in_cash=net_change_in_cash,
            capex=capex,
            dividends=dividends,
            filing=artifact,
            fraud_flag=fraud,
        )
        self._period_results[period] = result
        self._last_period_end = period_end
        return result

    @staticmethod
    def _decimal_map(values: dict[str, Decimal]) -> dict[str, str]:
        return {key: decimal_text(values[key]) for key in sorted(values)}

    def _period_evidence(self, result: AccountingQuarterV1) -> dict[str, Any]:
        net_change = result.net_change_in_cash
        return {
            "company": result.company,
            "sector": result.sector,
            "period": result.period,
            "period_start": result.period_start.isoformat(),
            "period_end": result.period_end.isoformat(),
            "available_date": result.available_date.isoformat(),
            "available_at": result.available_at,
            "income_statement": self._decimal_map(result.income_statement),
            "balance_sheet": self._decimal_map(result.balance_sheet),
            "cash_flow_direct": self._decimal_map(result.direct_cash_flow),
            "cash_flow_indirect": self._decimal_map(result.indirect_cash_flow),
            "weighted_average_shares": decimal_text(result.weighted_average_shares),
            "basic_eps": decimal_text(result.basic_eps),
            "opening_cash": decimal_text(result.opening_cash),
            "closing_cash": decimal_text(result.closing_cash),
            "net_change_in_cash": decimal_text(net_change),
            "capex": decimal_text(result.capex),
            "dividends": decimal_text(result.dividends),
            "fraud_flag": result.fraud_flag,
            "filing": {
                "filing_id": result.filing.filing_id,
                "version_id": result.filing.version_id,
                "version": result.filing.version,
                "form": result.filing.form.value,
                "period": result.filing.period,
                "period_end": result.filing.period_end.isoformat(),
                "available_at": result.filing.available_at,
                "payload_sha256": result.filing.payload_sha256,
            },
            "checks": {
                "balance_identity_exact": (
                    result.balance_sheet["total_assets"]
                    == result.balance_sheet["total_liabilities"] + result.balance_sheet["total_equity"]
                ),
                "cash_roll_exact": result.opening_cash + net_change == result.closing_cash,
                "direct_indirect_exact": result.direct_cash_flow == result.indirect_cash_flow,
            },
        }

    def evidence(self) -> dict[str, Any]:
        """Return deterministic exact-Decimal hidden accounting evidence."""
        entries = []
        for entry in self.ledger.entries:
            entries.append(
                {
                    "entry_id": entry.entry_id,
                    "entity_id": entry.entity_id,
                    "period": entry.period,
                    "event": entry.event,
                    "event_time": entry.event_time,
                    "posted_at": entry.posted_at,
                    "lines": [
                        {
                            "account": line.account,
                            "debit": decimal_text(line.debit),
                            "credit": decimal_text(line.credit),
                        }
                        for line in entry.lines
                    ],
                }
            )
        return {
            "entity_id": self.entity_id,
            "ticker": self.ticker,
            "configuration": {
                "sector": self.sector,
                "opening_date": self.opening_date.isoformat(),
                "horizon_quarters": self.period_horizon,
                "par_value": decimal_text(PAR_VALUE),
                "depreciation_rate": decimal_text(self.depreciation_rate),
                "useful_life_quarters": self.useful_life_periods,
                "opening_interest_rate": decimal_text(self.interest_rate),
                "tax_rate": decimal_text(self.tax_rate),
                "payout_ratio": decimal_text(self.payout_ratio),
                "minimum_cash_buffer_ratio": decimal_text(self.min_cash_buffer_ratio),
                "fraud_revenue_rate": decimal_text(FRAUD_REVENUE_RATE),
            },
            "opening": {
                key: decimal_text(value) if isinstance(value, Decimal) else value
                for key, value in sorted(self.opening_state.items())
            },
            "opening_targets": {
                key: decimal_text(value) if isinstance(value, Decimal) else value
                for key, value in sorted(self.opening_targets.items())
            },
            "journal_entries": entries,
            "receivables": [
                {
                    "invoice_id": invoice.invoice_id,
                    "entity_id": invoice.entity_id,
                    "issued_period": invoice.issued_period,
                    "due_period": invoice.due_period,
                    "original_amount": decimal_text(invoice.original_amount),
                    "outstanding_amount": decimal_text(invoice.outstanding_amount),
                }
                for invoice in sorted(self.operational.receivables.values(), key=lambda item: item.invoice_id)
            ],
            "payables": [
                {
                    "invoice_id": invoice.invoice_id,
                    "entity_id": invoice.entity_id,
                    "issued_period": invoice.issued_period,
                    "due_period": invoice.due_period,
                    "original_amount": decimal_text(invoice.original_amount),
                    "outstanding_amount": decimal_text(invoice.outstanding_amount),
                }
                for invoice in sorted(self.operational.payables.values(), key=lambda item: item.invoice_id)
            ],
            "inventory_layers": [
                {
                    "layer_id": layer.layer_id,
                    "acquired_period": layer.acquired_period,
                    "original_cost": decimal_text(layer.original_cost),
                    "remaining_cost": decimal_text(layer.remaining_cost),
                }
                for layer in sorted(
                    self.operational.inventory_layers.values(), key=lambda item: item.layer_id
                )
            ],
            "ppe_assets": [
                {
                    "asset_id": asset.asset_id,
                    "entity_id": asset.entity_id,
                    "placed_in_service_period": asset.placed_in_service_period,
                    "gross_cost": decimal_text(asset.gross_cost),
                    "residual_value": decimal_text(asset.residual_value),
                    "useful_life_periods": asset.useful_life_periods,
                    "accumulated_depreciation": decimal_text(asset.accumulated_depreciation),
                }
                for asset in sorted(self.operational.ppe_assets.values(), key=lambda item: item.asset_id)
            ],
            "debt_tranches": [
                {
                    "tranche_id": tranche.tranche_id,
                    "entity_id": tranche.entity_id,
                    "issued_period": tranche.issued_period,
                    "maturity_period": tranche.maturity_period,
                    "original_principal": decimal_text(tranche.original_principal),
                    "principal_outstanding": decimal_text(tranche.principal_outstanding),
                    "annual_rate": decimal_text(tranche.annual_rate),
                }
                for tranche in sorted(
                    self.operational.debt_tranches.values(), key=lambda item: item.tranche_id
                )
            ],
            "accrued_interest": [
                {
                    "tranche_id": schedule.tranche_id,
                    "period": schedule.period,
                    "opening_principal": decimal_text(schedule.opening_principal),
                    "accrued_amount": decimal_text(schedule.accrued_amount),
                    "paid_amount": decimal_text(schedule.paid_amount),
                    "ending_principal": decimal_text(schedule.ending_principal),
                }
                for _, schedule in sorted(self.operational.accrued_interest.items())
            ],
            "equity": {
                "opening_state": {
                    "issued_shares": self.opening_state["issued_shares"],
                    "treasury_shares": 0,
                    "common_stock": decimal_text(self.opening_state["common_stock"]),
                    "additional_paid_in_capital": decimal_text(
                        self.opening_state["additional_paid_in_capital"]
                    ),
                    "treasury_stock": decimal_text(self.opening_state["treasury_stock"]),
                },
                "share_events": [
                    {
                        "event_id": event.event_id,
                        "kind": event.kind.value,
                        "effective_date": event.effective_date.isoformat(),
                        "period": event.period,
                        "shares": event.shares,
                        "price": decimal_text(event.price),
                        "factor": f"{event.factor.numerator}/{event.factor.denominator}",
                        "event_time": event.event_time,
                        "posted_at": event.posted_at,
                    }
                    for event in self.equity.events
                ],
                "share_counts": {
                    "issued_shares": self.equity.issued_shares,
                    "treasury_shares": self.equity.treasury_shares,
                    "outstanding_shares": self.equity.outstanding_shares,
                },
            },
            "periods": [self._period_evidence(result) for result in self.period_results],
            "reconciliations": {
                "operational_failures": sorted(self.operational.validate_reconciliations()),
                "equity_failures": sorted(self.equity.validate_reconciliations()),
                "filing_failures": sorted(self.filings.validate_reconciliations()),
            },
            "filing_versions": [
                {
                    "filing_id": version.filing_id,
                    "version_id": version.version_id,
                    "entity_id": version.entity_id,
                    "form": version.form.value,
                    "period": version.period,
                    "period_end": version.period_end.isoformat(),
                    "version": version.version,
                    "payload_sha256": version.payload_sha256,
                    "available_at": version.available_at,
                    "supersedes_version_id": version.supersedes_version_id,
                    "correction_class": (
                        version.correction_class.value if version.correction_class is not None else None
                    ),
                }
                for version in self.filings.versions
            ],
            "non_reliance_notices": [
                {
                    "notice_id": notice.notice_id,
                    "entity_id": notice.entity_id,
                    "affected_version_ids": list(notice.affected_version_ids),
                    "conclusion_at": notice.conclusion_at,
                    "available_at": notice.available_at,
                    "reason": notice.reason,
                }
                for notice in self.filings.non_reliance_notices
            ],
            "filing_payloads": [
                {
                    "filing_id": artifact.filing_id,
                    "payload": artifact.payload,
                    "payload_sha256": artifact.payload_sha256,
                }
                for artifact in self.filing_artifacts
            ],
        }


__all__ = [
    "AccountingQuarterV1",
    "CompanyAccountingV1",
    "DEFAULT_USEFUL_LIFE_QUARTERS",
    "FilingArtifactV1",
    "accounting_instant",
    "canonical_payload_hash",
    "close_instant",
    "decimal_text",
    "exact_money",
    "exact_rate",
    "filing_instant",
    "positive_money",
    "quarter_bounds",
    "useful_life_quarters",
]
