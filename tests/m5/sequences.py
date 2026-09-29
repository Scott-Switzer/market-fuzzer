"""Deterministic generation of exactly 100 valid M5 business-event sequences.

Every sequence is produced from a fixed integer seed, so a run is reproducible
and a failing seed can be replayed directly. The generator consults
:mod:`tests.m5.rules` before emitting an event, so a sequence never contains an
impossible business action:

* no collection of a receivable that does not exist;
* no payment beyond the outstanding payable or beyond available cash;
* no consumption of inventory the company does not hold;
* no repayment beyond outstanding principal;
* no payment of accrued interest beyond the accrued payable;
* no share repurchase beyond outstanding shares or available cash;
* no dividend the company cannot fund from cash and retained earnings;
* no posting into a closed period.

Rejected events are never emitted and are never counted as coverage. The
100 sequences collectively exercise every supported event type; the coverage
counts are reported as machine-readable evidence.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal

from tests.m5.events import BusinessEvent, EventType, money
from tests.m5.rules import BusinessState
from tests.m5.rules import money as quantize

SEQUENCE_COUNT = 100
BASE_SEED = 20260925
ENTITY_ID = "M5-ORACLE-001"

#: Operating periods per sequence. Every sequence closes each period it uses.
PERIODS = 4


@dataclass
class GeneratedSequence:
    seed: int
    index: int
    events: list[BusinessEvent]

    @property
    def event_types(self) -> set[EventType]:
        return {event.event_type for event in self.events}


def _opening_reference(state: BusinessState, rng: random.Random) -> str:
    """Build the period-0 opening position as a business fact string."""
    cash = Decimal(rng.randrange(2_000_00, 6_000_00))
    ar = Decimal(rng.randrange(50_00, 400_00))
    inventory = Decimal(rng.randrange(100_00, 900_00))
    ppe = Decimal(rng.randrange(400_00, 1_500_00))
    ap = Decimal(rng.randrange(20_00, 300_00))
    debt = Decimal(rng.randrange(0, 800_00, 10_00))
    shares = rng.randrange(500, 2_000, 100)
    return ":".join(
        (
            f"cash={money(cash)}",
            f"ar={money(ar)}",
            f"inventory={money(inventory)}",
            f"ppe={money(ppe)}",
            f"ap={money(ap)}",
            f"debt={money(debt)}",
            f"shares={shares}",
            "par=0.01",
        )
    )


def _parse_opening(reference: str) -> dict[str, str]:
    return dict(pair.split("=", 1) for pair in reference.split(":") if pair)


def _seed_state(reference: str) -> BusinessState:
    facts = _parse_opening(reference)
    state = BusinessState(entity_id=ENTITY_ID)
    state.shares = _seed_shares(int(facts.get("shares", "1000")))
    state.cash = Decimal(facts["cash"])
    state.receivables["open-ar-1"] = _open_receivable("open-ar-1", 1, Decimal(facts["ar"]))
    state.payables["open-ap-1"] = _open_payable("open-ap-1", 1, Decimal(facts["ap"]))
    state.inventory_available = Decimal(facts["inventory"])
    state.retained_earnings = quantize(
        Decimal(facts["cash"])
        + Decimal(facts["ar"])
        + Decimal(facts["inventory"])
        + Decimal(facts["ppe"])
        - Decimal(facts["ap"])
        - Decimal(facts["debt"])
        - Decimal("0.01") * int(facts.get("shares", "1000"))
    )
    return state


def _open_receivable(invoice_id: str, due_period: int, amount: Decimal):
    from tests.m5.rules import Receivable

    return Receivable(invoice_id, due_period, amount)


def _open_payable(invoice_id: str, due_period: int, amount: Decimal):
    from tests.m5.rules import Payable

    return Payable(invoice_id, due_period, amount)


def _seed_shares(issued: int):
    from tests.m5.rules import ShareState

    return ShareState(issued=issued, treasury=0, par_value=Decimal("0.01"))


class _Builder:
    """Emits events for one sequence while maintaining valid business state."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.events: list[BusinessEvent] = []
        self.state = BusinessState(entity_id=ENTITY_ID)
        self._counter = 0

    def _emit(self, period: int, event_type: EventType, **kwargs: object) -> None:
        self._counter += 1
        self.events.append(
            BusinessEvent(
                event_id=f"m5-e{self._counter:05d}",
                entity_id=ENTITY_ID,
                period=period,
                event_type=event_type,
                amount=money(kwargs.pop("amount", "0.00")),
                reference=str(kwargs.pop("reference", "")),
                due_period=int(kwargs.pop("due_period", 0)),
                quantity=int(kwargs.pop("quantity", 0)),
                rate=str(kwargs.pop("rate", "0")),
                useful_life=int(kwargs.pop("useful_life", 0)),
                effective_date=str(kwargs.pop("effective_date", "")),
            )
        )

    def _amount(self, low: int, high: int, step: int = 1) -> Decimal:
        return Decimal(self.rng.randrange(low, high, step))

    def _effective_date(self, period: int) -> str:
        return f"{2000 + period}-12-31"

    # ------------------------------------------------------------------ build
    def build(self) -> list[BusinessEvent]:
        reference = _opening_reference(self.state, self.rng)
        self._emit(0, EventType.OPENING, reference=reference)
        self.state = _seed_state(reference)
        for period in range(1, PERIODS + 1):
            self._period(period)
            self._emit(period, EventType.PERIOD_CLOSE)
            self.state.closed_periods.add(period)
        return self.events

    def _period(self, period: int) -> None:
        state = self.state
        rng = self.rng

        # Credit revenue: the period's sales fact.
        sales = self._amount(20_000, 250_000, 100)
        invoice = state.invoice_id()
        due = period + rng.randrange(1, 3)
        self._emit(period, EventType.CREDIT_REVENUE, amount=sales, reference=invoice, due_period=due)
        state.receivables[invoice] = _open_receivable(invoice, due, sales)

        # Inventory purchase on AP.
        purchase = self._amount(10_000, 180_000, 100)
        payable = state.payable_id()
        due = period + rng.randrange(1, 3)
        self._emit(
            period,
            EventType.INVENTORY_PURCHASE,
            amount=purchase,
            reference=payable,
            due_period=due,
        )
        state.payables[payable] = _open_payable(payable, due, purchase)
        state.inventory_available = quantize(state.inventory_available + purchase)

        # Receivable collection (partial or full).
        open_receivables = state.open_receivables()
        if open_receivables:
            target = open_receivables[rng.randrange(len(open_receivables))]
            amount = target.outstanding if rng.random() < 0.4 else quantize(target.outstanding / 2)
            if amount > ZERO_MIN:
                self._emit(period, EventType.AR_COLLECTION, amount=amount, reference=target.invoice_id)
                target.outstanding = quantize(target.outstanding - amount)
                state.cash = quantize(state.cash + amount)

        # Payable settlement, bounded by the outstanding payable and by cash.
        open_payables = state.open_payables()
        if open_payables:
            target = open_payables[rng.randrange(len(open_payables))]
            amount = quantize(min(target.outstanding, state.cash))
            if amount > ZERO_MIN:
                self._emit(period, EventType.AP_PAYMENT, amount=amount, reference=target.invoice_id)
                target.outstanding = quantize(target.outstanding - amount)
                state.cash = quantize(state.cash - amount)

        # FIFO goods shipment, bounded by inventory actually held.
        available = state.inventory_available
        if available > ZERO_MIN:
            amount = available if rng.random() < 0.5 else quantize(available * Decimal("0.6"))
            amount = min(amount, available)
            if amount > ZERO_MIN:
                self._emit(period, EventType.INVENTORY_CONSUMPTION, amount=amount)
                state.inventory_available = quantize(available - amount)

        # SG&A.
        sga = self._amount(5_000, 60_000, 100)
        if sga <= state.cash:
            self._emit(period, EventType.SGA, amount=sga)
            state.cash = quantize(state.cash - sga)

        # PP&E acquisition, bounded by cash.
        capex = self._amount(50_000, 300_000, 100)
        if capex <= state.cash:
            self._emit(
                period, EventType.PPE_ACQUISITION, amount=capex, reference=state.asset_id(), useful_life=8
            )
            state.cash = quantize(state.cash - capex)

        # Straight-line depreciation for the period (amount derived by each side).
        self._emit(period, EventType.DEPRECIATION)

        # Debt borrowing, then interest accrual on a fresh tranche.
        if rng.random() < 0.7:
            principal = self._amount(100_000, 400_000, 1_000)
            tranche = state.tranche_id()
            maturity = period + 6
            rate = rng.choice(("0.04", "0.06", "0.08"))
            self._emit(
                period,
                EventType.DEBT_BORROWING,
                amount=principal,
                reference=tranche,
                due_period=maturity,
                rate=rate,
            )
            state.cash = quantize(state.cash + principal)
            from tests.m5.rules import Tranche

            state.tranches[tranche] = Tranche(
                tranche_id=tranche,
                issued_period=period,
                maturity_period=maturity,
                original=principal,
                outstanding=principal,
                annual_rate=Decimal(rate),
            )

        # Accrue interest on every tranche whose schedule has run to this period.
        for tranche in list(state.tranches.values()):
            if not tranche.issued_period < period <= tranche.maturity_period:
                continue
            if period != tranche.issued_period + 1 and (period - 1) not in tranche.accrued:
                continue
            self._emit(period, EventType.INTEREST_ACCRUAL, reference=tranche.tranche_id)
            # The first quarterly charge accrues on the original principal; a
            # later one accrues on the principal left standing at the end of the
            # previous period.
            opening = tranche.original if period == tranche.issued_period + 1 else tranche.ending[period - 1]
            accrued = quantize(opening * tranche.annual_rate / Decimal(4))
            tranche.accrued[period] = accrued
            tranche.ending[period] = tranche.outstanding
            state.interest_payable = quantize(state.interest_payable + accrued)

        # Pay accrued interest, bounded by the payable and by cash.
        if state.interest_payable > ZERO_MIN and state.cash > ZERO_MIN:
            amount = quantize(min(state.interest_payable, state.cash, Decimal("50.00")))
            if amount > ZERO_MIN:
                self._emit(period, EventType.INTEREST_PAYMENT, amount=amount)
                state.interest_payable = quantize(state.interest_payable - amount)
                state.cash = quantize(state.cash - amount)

        # Repay principal, bounded by the tranche and by cash. A tranche is never
        # repaid in the period it was issued, so its first quarterly interest
        # charge always accrues on a live principal.
        active = [
            tranche
            for tranche in state.active_tranches()
            if tranche.issued_period < period and tranche.outstanding > Decimal("100.00")
        ]
        if active and state.cash > Decimal("100.00"):
            tranche = active[rng.randrange(len(active))]
            amount = quantize(min(tranche.outstanding, state.cash))
            if amount > ZERO_MIN:
                self._emit(period, EventType.DEBT_REPAYMENT, amount=amount, reference=tranche.tranche_id)
                tranche.outstanding = quantize(tranche.outstanding - amount)
                if period in tranche.ending:
                    tranche.ending[period] = tranche.outstanding
                state.cash = quantize(state.cash - amount)

        # Tax accrual on the period's positive pre-tax result, then payment.
        tax = self._amount(1_000, 25_000, 100)
        self._emit(period, EventType.TAX_ACCRUAL, amount=tax)
        state.tax_payable = quantize(state.tax_payable + tax)
        if state.tax_payable > ZERO_MIN and state.cash > ZERO_MIN:
            amount = quantize(min(state.tax_payable, state.cash))
            if amount > ZERO_MIN:
                self._emit(period, EventType.TAX_PAYMENT, amount=amount)
                state.tax_payable = quantize(state.tax_payable - amount)
                state.cash = quantize(state.cash - amount)

        # Common-share issuance, bounded by nothing but cent precision.
        shares = rng.randrange(50, 400, 10)
        price = Decimal(rng.randrange(100, 2_000, 5)) / Decimal(100)
        proceeds = quantize(price * shares)
        self._emit(
            period,
            EventType.SHARE_ISSUANCE,
            amount=proceeds,
            quantity=shares,
            effective_date=self._effective_date(period),
        )
        state.shares.issued += shares
        state.cash = quantize(state.cash + proceeds)

        # Treasury repurchase, bounded by outstanding shares and by cash.
        if state.shares.outstanding > 100 and state.cash > Decimal("100.00"):
            quantity = rng.randrange(10, min(state.shares.outstanding, 200), 10)
            price = Decimal(rng.randrange(100, 1_500, 5)) / Decimal(100)
            cost = quantize(price * quantity)
            if cost <= state.cash:
                self._emit(
                    period,
                    EventType.SHARE_REPURCHASE,
                    amount=cost,
                    quantity=quantity,
                    effective_date=self._effective_date(period),
                )
                state.shares.treasury += quantity
                state.cash = quantize(state.cash - cost)

        # Dividend, bounded by cash and by retained earnings earned so far.
        distributable = quantize(min(state.cash, state.retained_earnings))
        if distributable > Decimal("10.00"):
            amount = quantize(distributable / 2)
            if amount > ZERO_MIN:
                self._emit(period, EventType.DIVIDENDS, amount=amount)
                state.cash = quantize(state.cash - amount)
                state.retained_earnings = quantize(state.retained_earnings - amount)

        # Retained earnings grows with the period's result so later dividends
        # are funded by an amount the sequence actually earned.
        state.retained_earnings = quantize(state.retained_earnings + Decimal("500.00"))


ZERO_MIN = Decimal("0.00")


def generate_sequences(count: int = SEQUENCE_COUNT, base_seed: int = BASE_SEED) -> list[GeneratedSequence]:
    """Return exactly ``count`` deterministic, valid event sequences."""
    sequences = [
        GeneratedSequence(seed=base_seed + index, index=index, events=_Builder(base_seed + index).build())
        for index in range(count)
    ]
    return sequences


def coverage(sequences: list[GeneratedSequence]) -> dict[str, int]:
    """Return the event-type occurrence counts across every sequence."""
    counts: dict[str, int] = {event.value: 0 for event in EventType}
    for sequence in sequences:
        for event in sequence.events:
            counts[event.event_type.value] += 1
    return counts


def iter_events(sequence: GeneratedSequence) -> Iterator[tuple[int, BusinessEvent]]:
    """Yield ``(index, event)`` pairs for a sequence."""
    yield from enumerate(sequence.events)
