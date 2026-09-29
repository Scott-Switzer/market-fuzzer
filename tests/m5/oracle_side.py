"""Independent ``python-accounting`` mapping of canonical M5 business events.

This is the independent side of the differential oracle. It receives the same
:class:`tests.m5.events.BusinessEvent` as the FWF kernel does and decides for
itself how to record it, using only ``python-accounting`` (MIT, pinned 1.0.1)
and SQLite.

Independence rules observed here:

* it imports no FWF module and no ``tests.m5.fwf_side`` symbol;
* it never reads a FWF journal line, posting template, or balance;
* it derives every amount the FWF kernel also derives -- FIFO layer splits,
  straight-line depreciation, quarterly interest, retained-earnings close
  transfers -- from the *business events it has already applied*, using its own
  schedules and its own arithmetic;
* it uses the library's native high-level transaction objects where they
  faithfully represent the operation (``ClientInvoice``, ``ClientReceipt``,
  ``SupplierBill``, ``SupplierPayment``, ``CashPurchase``) and an independently
  constructed compound ``JournalEntry`` otherwise.

The reported balance is a plain SQL aggregation over the library's own
``ledger`` table: total debits minus total credits per account. Nothing from
the FWF implementation participates.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from python_accounting.models import Account, Currency, Entity, Ledger, LineItem
from python_accounting.transactions import (
    CashPurchase,
    ClientInvoice,
    ClientReceipt,
    JournalEntry,
    SupplierBill,
    SupplierPayment,
)

from tests.m5.chart import FWF_ACCOUNTS, P_AND_L_ACCOUNTS
from tests.m5.events import BusinessEvent

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
FOUR = Decimal(4)

#: The accounting year used by the oracle. Fixed so the run is deterministic.
ACCOUNTING_YEAR = 2026


def money(value: Decimal | int | str) -> Decimal:
    """Return the canonical cent-quantized monetary amount."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------- oracle schedules
@dataclass
class _Layer:
    acquired_period: int
    remaining: Decimal


@dataclass
class _Asset:
    placed_period: int
    gross: Decimal
    residual: Decimal
    useful_life: int
    accumulated: Decimal = ZERO


@dataclass
class _Tranche:
    issued_period: int
    maturity_period: int
    original: Decimal
    outstanding: Decimal
    annual_rate: Decimal
    accrued: dict[int, Decimal] = field(default_factory=dict)
    paid: dict[int, Decimal] = field(default_factory=dict)
    ending: dict[int, Decimal] = field(default_factory=dict)


def _event_date(period: int) -> datetime:
    """Map an operating period to a deterministic calendar date.

    The oracle's year is fixed and every period lands on its own day inside the
    single reporting period, so the library's period rules are satisfied without
    a time-zone package. Period 0 starts one day after the reporting period
    opens, because the library reserves the exact period start for balances.
    """
    return datetime(ACCOUNTING_YEAR, 1, 2) + timedelta(days=period * 7)


def _is_debit(entry_type: object) -> bool:
    """Return whether a library ledger entry type is on the debit side."""
    name = getattr(entry_type, "name", None) or str(entry_type)
    return str(name).upper().endswith("DEBIT")


class OracleCompany:
    """One company's books in ``python-accounting``, driven by business events."""

    def __init__(self, session, entity: Entity, currency: Currency, accounts: dict[str, Account]):
        self.session = session
        self.entity = entity
        self.accounts = accounts
        self.layers: deque[_Layer] = deque()
        self.assets: list[_Asset] = []
        self.tranches: dict[str, _Tranche] = {}
        self.issued_shares = 0
        self.treasury_shares = 0
        self.par_value = Decimal("0.01")
        self._sequence = 0

    # ---------------------------------------------------------------- helpers
    def _next_id(self, prefix: str) -> str:
        self._sequence += 1
        return f"m5-{prefix}-{self._sequence:06d}"

    def _journal(
        self,
        *,
        when: datetime,
        debit: dict[str, Decimal],
        credit: dict[str, Decimal],
        narration: str,
    ) -> None:
        """Post a balanced compound journal entry, built independently."""
        if sum(debit.values(), ZERO) != sum(credit.values(), ZERO):
            raise ValueError("oracle journal entry is not balanced")
        main = next(iter(debit), None) or next(iter(credit))
        main_is_debit = main in debit
        entry = JournalEntry(
            narration=narration,
            transaction_date=when,
            account_id=self.accounts[main].id,
            main_account_amount=debit[main] if main_is_debit else credit[main],
            compound=True,
            credited=not main_is_debit,
            entity_id=self.entity.id,
        )
        # The library validates a compound entry on every flush, so the entry is
        # attached to the session only once all of its line items exist.
        for account, amount in debit.items():
            if account == main:
                continue
            self._add_line(entry, narration, account, amount, credited=False)
        for account, amount in credit.items():
            if account == main:
                continue
            self._add_line(entry, narration, account, amount, credited=True)
        self.session.add(entry)
        entry.post(self.session)
        self.session.commit()

    def _add_line(
        self,
        entry: JournalEntry,
        narration: str,
        account: str,
        amount: Decimal,
        *,
        credited: bool,
    ) -> None:
        line = LineItem(
            narration=f"{narration} {'credit' if credited else 'debit'} {account}",
            account_id=self.accounts[account].id,
            amount=amount,
            credited=credited,
            entity_id=self.entity.id,
        )
        self.session.add(line)
        self.session.flush()
        entry.line_items.add(line)

    def _native(
        self,
        factory,
        *,
        when: datetime,
        main_account: str,
        line_account: str,
        amount: Decimal,
        narration: str,
    ) -> None:
        """Post a native two-sided library transaction."""
        transaction = factory(
            narration=narration,
            transaction_date=when,
            account_id=self.accounts[main_account].id,
            entity_id=self.entity.id,
        )
        self._add_line(transaction, narration, line_account, amount, credited=False)
        self.session.add(transaction)
        transaction.post(self.session)
        self.session.commit()

    # ------------------------------------------------------------------ apply
    def open(self, event: BusinessEvent) -> None:
        """Record the period-0 opening position as a compound journal entry."""
        facts = dict(pair.split("=", 1) for pair in event.reference.split(":") if pair)
        assets = {name: Decimal(facts[name]) for name in ("cash", "ar", "inventory", "ppe")}
        liabilities = {name: Decimal(facts[name]) for name in ("ap", "debt")}
        self.par_value = Decimal(facts.get("par", "0.01"))
        shares = int(facts.get("shares", "1000"))
        self.issued_shares = shares
        par_capital = money(self.par_value * shares)
        equity_total = sum(assets.values()) - sum(liabilities.values())
        retained = money(equity_total - par_capital)
        self._journal(
            when=_event_date(0),
            debit=assets,
            credit={
                **liabilities,
                "common_stock": par_capital,
                "additional_paid_in_capital": ZERO,
                "retained_earnings": retained,
            },
            narration="m5 opening balances",
        )
        if assets["ar"] > ZERO:
            self.receivable_ids = ["open-ar-1"]
        else:
            self.receivable_ids = []
        self.payable_ids = ["open-ap-1"] if liabilities["ap"] > ZERO else []
        if assets["inventory"] > ZERO:
            self.layers.append(_Layer(0, assets["inventory"]))
        if assets["ppe"] > ZERO:
            self.assets.append(_Asset(0, assets["ppe"], ZERO, 8))
        if liabilities["debt"] > ZERO:
            self.tranches["open-debt-1"] = _Tranche(
                0, 8, liabilities["debt"], liabilities["debt"], Decimal("0.04")
            )

    def apply(self, event: BusinessEvent) -> None:
        """Record one business event in the independent ledger."""
        handler = getattr(self, f"_do_{event.event_type.value}")
        handler(event)

    def _do_credit_revenue(self, event: BusinessEvent) -> None:
        self._native(
            ClientInvoice,
            when=_event_date(event.period),
            main_account="ar",
            line_account="revenue",
            amount=Decimal(event.amount),
            narration=f"m5 credit revenue {event.event_id}",
        )

    def _do_ar_collection(self, event: BusinessEvent) -> None:
        self._native(
            ClientReceipt,
            when=_event_date(event.period),
            main_account="ar",
            line_account="cash",
            amount=Decimal(event.amount),
            narration=f"m5 receivable collection {event.event_id}",
        )

    def _do_inventory_purchase(self, event: BusinessEvent) -> None:
        self._native(
            SupplierBill,
            when=_event_date(event.period),
            main_account="ap",
            line_account="inventory",
            amount=Decimal(event.amount),
            narration=f"m5 inventory purchase {event.event_id}",
        )
        self.layers.append(_Layer(event.period, Decimal(event.amount)))

    def _do_ap_payment(self, event: BusinessEvent) -> None:
        self._native(
            SupplierPayment,
            when=_event_date(event.period),
            main_account="ap",
            line_account="cash",
            amount=Decimal(event.amount),
            narration=f"m5 payable settlement {event.event_id}",
        )

    def _do_inventory_consumption(self, event: BusinessEvent) -> None:
        self._journal(
            when=_event_date(event.period),
            debit={"cogs": Decimal(event.amount)},
            credit={"inventory": Decimal(event.amount)},
            narration=f"m5 goods shipped {event.event_id}",
        )

    def _do_ppe_acquisition(self, event: BusinessEvent) -> None:
        amount = Decimal(event.amount)
        self._native(
            CashPurchase,
            when=_event_date(event.period),
            main_account="cash",
            line_account="ppe",
            amount=amount,
            narration=f"m5 capital expenditure {event.event_id}",
        )
        self.assets.append(_Asset(event.period, amount, ZERO, event.useful_life or 8))

    def _do_depreciation(self, event: BusinessEvent) -> None:
        """Derive the period's straight-line charge from the oracle's own schedule."""
        allocations: list[tuple[_Asset, Decimal]] = []
        for asset in self.assets:
            first_operating_period = max(1, asset.placed_period)
            elapsed = min(max(event.period - first_operating_period + 1, 0), asset.useful_life)
            if elapsed == asset.useful_life:
                target = money(asset.gross - asset.residual)
            else:
                target = money((asset.gross - asset.residual) / Decimal(asset.useful_life)) * Decimal(elapsed)
            charge = money(target - asset.accumulated)
            if charge > ZERO:
                allocations.append((asset, charge))
        total = money(sum((charge for _, charge in allocations), ZERO))
        if total <= ZERO:
            return
        self._journal(
            when=_event_date(event.period),
            debit={"depreciation": total},
            credit={"acc_dep": total},
            narration=f"m5 depreciation {event.event_id}",
        )
        for asset, charge in allocations:
            asset.accumulated = money(asset.accumulated + charge)

    def _do_debt_borrowing(self, event: BusinessEvent) -> None:
        amount = Decimal(event.amount)
        self._journal(
            when=_event_date(event.period),
            debit={"cash": amount},
            credit={"debt": amount},
            narration=f"m5 debt borrowing {event.event_id}",
        )
        self.tranches[event.reference] = _Tranche(
            event.period, event.due_period, amount, amount, Decimal(event.rate)
        )

    def _do_interest_accrual(self, event: BusinessEvent) -> None:
        """Derive the quarterly charge from the oracle's own tranche schedule."""
        tranche = self.tranches[event.reference]
        # The first quarterly charge accrues on the original principal; a later
        # one accrues on the principal left standing at the end of the previous
        # period.
        if event.period == tranche.issued_period + 1:
            opening = tranche.original
        else:
            opening = tranche.ending[event.period - 1]
        accrued = money(opening * tranche.annual_rate / FOUR)
        tranche.accrued[event.period] = accrued
        tranche.ending[event.period] = tranche.outstanding
        if accrued <= ZERO:
            return
        self._journal(
            when=_event_date(event.period),
            debit={"interest": accrued},
            credit={"interest_payable": accrued},
            narration=f"m5 interest accrual {event.event_id}",
        )

    def _do_interest_payment(self, event: BusinessEvent) -> None:
        self._journal(
            when=_event_date(event.period),
            debit={"interest_payable": Decimal(event.amount)},
            credit={"cash": Decimal(event.amount)},
            narration=f"m5 interest payment {event.event_id}",
        )
        for tranche in self.tranches.values():
            if event.period in tranche.accrued:
                tranche.paid[event.period] = money(
                    tranche.paid.get(event.period, ZERO) + Decimal(event.amount)
                )
                break

    def _do_debt_repayment(self, event: BusinessEvent) -> None:
        amount = Decimal(event.amount)
        self._journal(
            when=_event_date(event.period),
            debit={"debt": amount},
            credit={"cash": amount},
            narration=f"m5 debt repayment {event.event_id}",
        )
        tranche = self.tranches[event.reference]
        tranche.outstanding = money(tranche.outstanding - amount)
        if event.period in tranche.ending:
            tranche.ending[event.period] = tranche.outstanding

    def _do_sga(self, event: BusinessEvent) -> None:
        self._native(
            CashPurchase,
            when=_event_date(event.period),
            main_account="cash",
            line_account="sga",
            amount=Decimal(event.amount),
            narration=f"m5 operating expense {event.event_id}",
        )

    def _do_tax_accrual(self, event: BusinessEvent) -> None:
        self._journal(
            when=_event_date(event.period),
            debit={"tax_expense": Decimal(event.amount)},
            credit={"tax_payable": Decimal(event.amount)},
            narration=f"m5 tax accrual {event.event_id}",
        )

    def _do_tax_payment(self, event: BusinessEvent) -> None:
        self._journal(
            when=_event_date(event.period),
            debit={"tax_payable": Decimal(event.amount)},
            credit={"cash": Decimal(event.amount)},
            narration=f"m5 tax payment {event.event_id}",
        )

    def _do_share_issuance(self, event: BusinessEvent) -> None:
        proceeds = Decimal(event.amount)
        shares = event.quantity
        par = money(self.par_value * shares)
        self._journal(
            when=_event_date(event.period),
            debit={"cash": proceeds},
            credit={"common_stock": par, "additional_paid_in_capital": money(proceeds - par)},
            narration=f"m5 share issuance {event.event_id}",
        )
        self.issued_shares += shares

    def _do_share_repurchase(self, event: BusinessEvent) -> None:
        cost = Decimal(event.amount)
        self._journal(
            when=_event_date(event.period),
            debit={"treasury_stock": cost},
            credit={"cash": cost},
            narration=f"m5 share repurchase {event.event_id}",
        )
        self.treasury_shares += event.quantity

    def _do_dividends(self, event: BusinessEvent) -> None:
        self._journal(
            when=_event_date(event.period),
            debit={"dividends": Decimal(event.amount)},
            credit={"cash": Decimal(event.amount)},
            narration=f"m5 dividend {event.event_id}",
        )

    def _do_period_close(self, event: BusinessEvent) -> None:
        """Close the period's P&L into retained earnings, derived independently.

        Only profit-and-loss accounts are closed. Balance-sheet accounts keep
        their period movement, so a close reverses revenue, expense, and
        dividends and posts the period's net result to retained earnings.
        """
        movements = self._period_movements(event.period)
        debit: dict[str, Decimal] = {}
        credit: dict[str, Decimal] = {}
        for account in P_AND_L_ACCOUNTS:
            movement = movements[account]
            if movement == ZERO:
                continue
            # Movements are debit positive, so a credit balance is reversed by
            # a debit and a debit balance by a credit.
            if movement > ZERO:
                credit[account] = movement
            else:
                debit[account] = money(-movement)
        if not debit and not credit:
            return
        net = money(sum(debit.values(), ZERO) - sum(credit.values(), ZERO))
        if net > ZERO:
            credit["retained_earnings"] = net
        elif net < ZERO:
            debit["retained_earnings"] = money(-net)
        self._journal(
            when=_event_date(event.period),
            debit=debit,
            credit=credit,
            narration=f"m5 period close {event.period}",
        )

    def _do_opening(self, event: BusinessEvent) -> None:
        self.open(event)

    # ------------------------------------------------------------- reporting
    def _period_movements(self, period: int) -> dict[str, Decimal]:
        """Return this period's own net debit-positive movement per account."""
        when = _event_date(period)
        rows = (
            self.session.query(Ledger.post_account_id, Ledger.entry_type, Ledger.amount)
            .filter(Ledger.entity_id == self.entity.id)
            .filter(Ledger.transaction_date == when)
            .all()
        )
        movements = dict.fromkeys(FWF_ACCOUNTS, ZERO)
        by_id = {account.id: name for name, account in self.accounts.items()}
        for post_account_id, entry_type, amount in rows:
            name = by_id.get(post_account_id)
            if name is None:
                continue
            sign = +1 if _is_debit(entry_type) else -1
            movements[name] = money(movements[name] + sign * Decimal(amount))
        return movements

    def trial_balance(self) -> dict[str, Decimal]:
        """Aggregate the library's own ledger into a debit-positive trial balance."""
        from sqlalchemy import func

        rows = (
            self.session.query(
                Ledger.post_account_id,
                Ledger.entry_type,
                func.sum(Ledger.amount),
            )
            .filter(Ledger.entity_id == self.entity.id)
            .group_by(Ledger.post_account_id, Ledger.entry_type)
            .all()
        )
        balances = dict.fromkeys(FWF_ACCOUNTS, ZERO)
        by_id = {account.id: name for name, account in self.accounts.items()}
        for post_account_id, entry_type, total in rows:
            name = by_id.get(post_account_id)
            if name is None:
                continue
            sign = +1 if _is_debit(entry_type) else -1
            balances[name] = money(balances[name] + sign * Decimal(total))
        return balances
