"""Apply a canonical business event to the vendored FWF accounting kernel.

This side is the *system under test*. It uses only the public FWF interfaces
(``Ledger``, ``OperationalBook``, ``EquityBook``) that Market Fuzzer itself
consumes, and it reports a normalized, debit-positive trial balance over
:data:`tests.m5.chart.FWF_ACCOUNTS`.

It never inspects or imports anything from ``python-accounting``; the two sides
share only :mod:`tests.m5.events`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app._vendor.fwf_kernel import equity as E
from app._vendor.fwf_kernel import ledger as L
from app._vendor.fwf_kernel import subledgers as S
from tests.m5.chart import FWF_ACCOUNTS, to_debit_positive
from tests.m5.events import BusinessEvent

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

#: Base date for period 0. Operating period ``p`` is dated in year 2000 + p so
#: that every period has a distinct, deterministic calendar year and equity
#: effective dates never collide.
BASE_YEAR = 2000

#: Straight-line useful life applied to acquired PP&E, in quarters.
PPE_USEFUL_LIFE = 8


def period_end_date(period: int) -> date:
    """Return the deterministic calendar date used for accounting period ``p``."""
    if period == 0:
        return date(BASE_YEAR, 12, 31)
    return date(BASE_YEAR + period, 12, 31)


def period_instant(period: int, slot: int = 0) -> str:
    """Return a canonical UTC whole-second instant inside period ``p``.

    Slots map onto a bounded afternoon window so a long period can never
    overflow the day. Period close uses :func:`close_instant`, which is always
    the last instant of the quarter-end date.
    """
    stamp = period_end_date(period)
    hour = 12 + slot // 4
    minute = (slot % 4) * 15
    return f"{stamp.isoformat()}T{hour:02d}:{minute:02d}:00Z"


def close_instant(period: int) -> str:
    """Return the period-close instant, after every period transaction."""
    return f"{period_end_date(period).isoformat()}T23:59:59Z"


@dataclass
class FwfCompany:
    """One company's FWF accounting books, driven purely by business events."""

    entity_id: str
    ledger: L.Ledger = field(init=False)
    operations: S.OperationalBook = field(init=False)
    equity: E.EquityBook = field(init=False)
    issued_shares: int = 0
    par_value: str = "0.01"
    _slot: int = 0

    def __post_init__(self) -> None:
        self.ledger = L.Ledger(self.entity_id)

    # ---------------------------------------------------------------- opening
    def open(self, event: BusinessEvent) -> None:
        """Post the period-0 opening entry and build the operational books.

        ``event.reference`` carries a compact, deterministic description of the
        opening balance sheet: ``cash|ar|inventory|ppe|ap|debt`` amounts, then
        ``:``-separated share/equity targets. The values are business facts, not
        postings; this method decides the debit/credit shape itself.
        """
        facts = dict(pair.split("=", 1) for pair in event.reference.split(":") if pair)
        self.issued_shares = int(facts.get("shares", "1000"))
        self.par_value = facts.get("par", "0.01")
        assets = {name: Decimal(facts[name]) for name in ("cash", "ar", "inventory", "ppe")}
        liabilities = {name: Decimal(facts[name]) for name in ("ap", "debt")}
        for name, value in {**assets, **liabilities}.items():
            if value < ZERO:
                raise ValueError(f"opening {name} must be non-negative")
        if any(value == ZERO for value in assets.values()):
            raise ValueError("every opening asset must be positive")

        # Retained earnings absorbs the residual so the opening entry balances
        # without a plug account: equity is derived from assets less liabilities.
        par = Decimal(self.par_value)
        par_capital = (par * self.issued_shares).quantize(CENT)
        equity_total = sum(assets.values()) - sum(liabilities.values())
        if equity_total < par_capital:
            raise ValueError("opening equity cannot cover par capital")
        retained = equity_total - par_capital
        apic = ZERO

        self.ledger.post(
            L.make(
                "m5-opening",
                self.entity_id,
                0,
                "opening",
                {name: value for name, value in assets.items()},
                {
                    **liabilities,
                    "common_stock": par_capital,
                    "retained_earnings": retained,
                    "additional_paid_in_capital": apic,
                },
                period_instant(0),
                period_instant(0),
            )
        )
        self.operations = S.OperationalBook(
            self.ledger,
            receivables=(S.ReceivableInvoice("open-ar-1", self.entity_id, 0, 1, assets["ar"], assets["ar"]),)
            if assets["ar"] > ZERO
            else (),
            payables=(
                S.PayableInvoice("open-ap-1", self.entity_id, 0, 1, liabilities["ap"], liabilities["ap"]),
            )
            if liabilities["ap"] > ZERO
            else (),
            inventory_layers=(S.InventoryLayer("open-inv-1", 0, assets["inventory"], assets["inventory"]),)
            if assets["inventory"] > ZERO
            else (),
            ppe_assets=(
                S.PPEAsset(
                    "open-ppe-1",
                    self.entity_id,
                    0,
                    assets["ppe"],
                    ZERO,
                    PPE_USEFUL_LIFE,
                    ZERO,
                ),
            )
            if assets["ppe"] > ZERO
            else (),
            debt_tranches=(
                S.DebtTranche(
                    "open-debt-1",
                    self.entity_id,
                    0,
                    PPE_USEFUL_LIFE,
                    liabilities["debt"],
                    liabilities["debt"],
                    Decimal("0.04"),
                ),
            )
            if liabilities["debt"] > ZERO
            else (),
        )
        self.equity = E.EquityBook(
            self.ledger,
            E.CommonShareClass("common", par),
            opening_state=E.OpeningEquityState(
                issued_shares=self.issued_shares,
                treasury_shares=0,
                common_stock=par_capital,
                additional_paid_in_capital=apic,
                treasury_stock=ZERO,
            ),
        )

    # ----------------------------------------------------------------- apply
    def apply(self, event: BusinessEvent) -> None:
        """Post one business event through the FWF kernel."""
        self._slot += 1
        slot = min(self._slot, 43)
        stamp = period_instant(event.period, slot)
        posted = period_instant(event.period, slot + 1)
        amount = Decimal(event.amount)
        handler = getattr(self, f"_do_{event.event_type.value}", None)
        if handler is None:
            raise ValueError(f"unsupported event type {event.event_type}")
        handler(event, amount, stamp, posted)

    def _do_credit_revenue(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        self.operations.issue_receivable(
            event.reference, self.entity_id, event.period, event.due_period, amount, stamp, posted
        )

    def _do_ar_collection(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        self.operations.collect_receivable(event.reference, amount, event.period, stamp, posted)

    def _do_inventory_purchase(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        self.operations.issue_inventory_payable(
            event.reference,
            f"{event.reference}-layer",
            self.entity_id,
            event.period,
            event.due_period,
            amount,
            stamp,
            posted,
        )

    def _do_ap_payment(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        self.operations.pay_payable(event.reference, amount, event.period, stamp, posted)

    def _do_inventory_consumption(
        self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str
    ) -> None:
        self.operations.consume_inventory(amount, event.period, stamp, posted)

    def _do_ppe_acquisition(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        self.operations.acquire_ppe_asset(
            event.reference,
            self.entity_id,
            event.period,
            amount,
            ZERO,
            event.useful_life or PPE_USEFUL_LIFE,
            stamp,
            posted,
        )

    def _do_depreciation(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        # The FWF kernel derives the period's depreciation from its own PP&E
        # schedule; the event carries no amount, only the fact that the period's
        # depreciation ran.
        self.operations.depreciate_ppe(event.period, stamp, posted)

    def _do_debt_borrowing(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        self.operations.borrow_debt(
            event.reference,
            self.entity_id,
            event.period,
            event.due_period,
            amount,
            event.rate,
            stamp,
            posted,
        )

    def _do_interest_accrual(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        # The accrued amount is derived by the kernel from the tranche schedule.
        self.operations.accrue_interest(event.reference, event.period, stamp, posted)

    def _do_interest_payment(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        self.operations.pay_accrued_interest(amount, event.period, stamp, posted)

    def _do_debt_repayment(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        self.operations.repay_debt(event.reference, amount, event.period, stamp, posted)

    def _do_sga(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        self.ledger.post(
            L.event(
                f"m5-sga-{event.event_id}",
                self.entity_id,
                event.period,
                "pay_sga",
                amount,
                stamp,
                posted,
            )
        )

    def _do_tax_accrual(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        self.ledger.post(
            L.event(
                f"m5-tax-{event.event_id}",
                self.entity_id,
                event.period,
                "accrue_tax",
                amount,
                stamp,
                posted,
            )
        )

    def _do_tax_payment(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        self.ledger.post(
            L.event(
                f"m5-taxpay-{event.event_id}",
                self.entity_id,
                event.period,
                "pay_tax",
                amount,
                stamp,
                posted,
            )
        )

    def _do_share_issuance(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        price = (amount / Decimal(event.quantity)).quantize(Decimal("0.0001"))
        self.equity.issue_common_shares(
            event.quantity,
            price,
            date.fromisoformat(event.effective_date),
            event.period,
            stamp,
            posted,
        )

    def _do_share_repurchase(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        price = (amount / Decimal(event.quantity)).quantize(Decimal("0.0001"))
        self.equity.repurchase_common_shares(
            event.quantity,
            price,
            date.fromisoformat(event.effective_date),
            event.period,
            stamp,
            posted,
        )

    def _do_dividends(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        self.ledger.post(
            L.event(
                f"m5-div-{event.event_id}",
                self.entity_id,
                event.period,
                "pay_dividend",
                amount,
                stamp,
                posted,
            )
        )

    def _do_period_close(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        self.ledger.close(event.period, close_instant(event.period))

    def _do_opening(self, event: BusinessEvent, amount: Decimal, stamp: str, posted: str) -> None:
        self.open(event)

    # ------------------------------------------------------------- reporting
    def trial_balance(self) -> dict[str, Decimal]:
        """Return the cumulative, debit-positive normalized trial balance."""
        balances = self.ledger.balances()
        return {name: to_debit_positive(name, balances[name]) for name in FWF_ACCOUNTS}

    def period_income(self, period: int) -> Decimal:
        """Return the period's pre-close net income, debit positive."""
        statement = L.income_statement(self.ledger, period)
        return statement["net_income"]
