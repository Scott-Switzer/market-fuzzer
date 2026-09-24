"""Reconciled operational accounting subledgers for Financial World Factory M4.1.

The ledger remains authoritative for journal construction, posting, and statement derivation.
This module owns detailed operational records and provides controlled, Decimal-only mutation paths
that keep each schedule exactly reconciled to its general-ledger control account.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal, DecimalException
from typing import Final

from . import ledger as L

CENT: Final = Decimal("0.01")
ZERO: Final = Decimal("0.00")
_RATE_QUANTUM: Final = Decimal("0.000000000001")

AR_RULE: Final = "ACCT-AR-SUBLEDGER"
AP_RULE: Final = "ACCT-AP-SUBLEDGER"
INVENTORY_RULE: Final = "ACCT-INVENTORY-FIFO"
PPE_RULE: Final = "ACCT-PPE-SCHEDULE"
DEBT_RULE: Final = "ACCT-DEBT-SCHEDULE"

RECONCILIATION_RULES: Final[dict[str, str]] = {
    AR_RULE: "open receivables equal the accounts-receivable control account",
    AP_RULE: "open payables equal the accounts-payable control account",
    INVENTORY_RULE: "FIFO layer cost equals the inventory control account",
    PPE_RULE: "PP&E gross and accumulated depreciation equal their control accounts",
    DEBT_RULE: "debt principal and unpaid accrued interest equal their control accounts",
}

type DecimalSource = str | int | Decimal
type AgingBuckets = dict[str, Decimal]
type RecordInput[T] = Mapping[str, T] | Iterable[T]


def _to_decimal(value: DecimalSource, quantum: Decimal) -> Decimal:
    """Convert an exact decimal source and reject binary floats and non-finite values."""
    if isinstance(value, (bool, float)):
        raise TypeError("binary floats are not accepted in accounting state")
    try:
        amount = Decimal(value)
    except (DecimalException, ValueError) as exc:
        raise ValueError(f"invalid decimal amount {value!r}") from exc
    if not amount.is_finite():
        raise ValueError("amount must be finite")
    if amount < 0:
        raise ValueError("amount must be non-negative")
    try:
        return amount.quantize(quantum, rounding=ROUND_HALF_UP)
    except DecimalException as exc:
        raise ValueError(f"amount cannot be represented at {quantum}") from exc


def money(value: DecimalSource, *, allow_zero: bool = True) -> Decimal:
    """Return the canonical cent-quantized monetary representation."""
    amount = _to_decimal(value, CENT)
    if not allow_zero and amount == ZERO:
        raise ValueError("amount must be positive")
    return amount


def _rate(value: DecimalSource) -> Decimal:
    return _to_decimal(value, _RATE_QUANTUM)


def _text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _record_period(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a record period >= 0")


def _period(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be an operating period >= 1")


def _copy_records[T](records: RecordInput[T], expected: type[T], id_field: str) -> dict[str, T]:
    copied: dict[str, T] = {}

    def add(key: str | None, record: object) -> None:
        if not isinstance(record, expected):
            raise TypeError(f"expected {expected.__name__} detail record")
        record_id = getattr(record, id_field, None)
        if not isinstance(record_id, str) or not record_id:
            raise ValueError(f"{id_field} must be a non-empty string")
        if key is not None and key != record_id:
            raise ValueError(f"detail mapping key does not match {id_field}")
        if record_id in copied:
            raise ValueError(f"duplicate detail record {record_id}")
        copied[record_id] = record

    if isinstance(records, Mapping):
        for key, record in records.items():
            add(key, record)
    else:
        for record in records:
            add(None, record)
    return copied


def _copy_interest_schedules(
    records: Mapping[tuple[str, int], AccruedInterestSchedule] | Iterable[AccruedInterestSchedule],
) -> dict[tuple[str, int], AccruedInterestSchedule]:
    copied: dict[tuple[str, int], AccruedInterestSchedule] = {}

    def add(key: object, schedule: object) -> None:
        if not isinstance(schedule, AccruedInterestSchedule):
            raise TypeError("expected AccruedInterestSchedule detail record")
        identity = (schedule.tranche_id, schedule.period)
        if key != identity:
            raise ValueError("interest schedule key does not match its record")
        if identity in copied:
            raise ValueError(f"duplicate interest schedule {identity}")
        copied[identity] = schedule

    if isinstance(records, Mapping):
        for key, schedule in records.items():
            add(key, schedule)
    else:
        for schedule in records:
            if not isinstance(schedule, AccruedInterestSchedule):
                raise TypeError("expected AccruedInterestSchedule detail record")
            add((schedule.tranche_id, schedule.period), schedule)
    return copied


def _unique_ids(records: Mapping[str, object], id_field: str) -> None:
    actual_ids = [getattr(record, id_field, None) for record in records.values()]
    if len(actual_ids) != len(set(actual_ids)):
        raise ValueError("record ids must be unique")
    for key, record_id in zip(records, actual_ids, strict=True):
        if key != record_id:
            raise ValueError(f"record key {key!r} does not match record id {record_id!r}")


@dataclass(frozen=True, slots=True)
class ReceivableInvoice:
    """Immutable invoice identity with ledger-controlled outstanding state."""

    invoice_id: str
    entity_id: str
    issued_period: int
    due_period: int
    original_amount: Decimal
    _outstanding_amount: Decimal = field(repr=False)

    def __post_init__(self) -> None:
        _text(self.invoice_id, "invoice_id")
        _text(self.entity_id, "entity_id")
        _record_period(self.issued_period, "issued_period")
        _record_period(self.due_period, "due_period")
        if self.due_period < self.issued_period:
            raise ValueError("due_period must be >= issued_period")
        original = money(self.original_amount, allow_zero=False)
        outstanding = money(self._outstanding_amount)
        if outstanding > original:
            raise ValueError("outstanding_amount cannot exceed original_amount")
        object.__setattr__(self, "original_amount", original)
        object.__setattr__(self, "_outstanding_amount", outstanding)

    @property
    def outstanding_amount(self) -> Decimal:
        return self._outstanding_amount

    @property
    def id(self) -> str:
        return self.invoice_id

    def _collected(self, amount: Decimal) -> ReceivableInvoice:
        return replace(self, _outstanding_amount=self.outstanding_amount - amount)


@dataclass(frozen=True, slots=True)
class PayableInvoice:
    """Immutable invoice identity with ledger-controlled outstanding state."""

    invoice_id: str
    entity_id: str
    issued_period: int
    due_period: int
    original_amount: Decimal
    _outstanding_amount: Decimal = field(repr=False)

    def __post_init__(self) -> None:
        _text(self.invoice_id, "invoice_id")
        _text(self.entity_id, "entity_id")
        _record_period(self.issued_period, "issued_period")
        _record_period(self.due_period, "due_period")
        if self.due_period < self.issued_period:
            raise ValueError("due_period must be >= issued_period")
        original = money(self.original_amount, allow_zero=False)
        outstanding = money(self._outstanding_amount)
        if outstanding > original:
            raise ValueError("outstanding_amount cannot exceed original_amount")
        object.__setattr__(self, "original_amount", original)
        object.__setattr__(self, "_outstanding_amount", outstanding)

    @property
    def outstanding_amount(self) -> Decimal:
        return self._outstanding_amount

    @property
    def id(self) -> str:
        return self.invoice_id

    def _paid(self, amount: Decimal) -> PayableInvoice:
        return replace(self, _outstanding_amount=self.outstanding_amount - amount)


@dataclass(frozen=True, slots=True)
class InventoryLayer:
    """Immutable cost-layer identity with a ledger-controlled remaining balance."""

    layer_id: str
    acquired_period: int
    original_cost: Decimal
    _remaining_cost: Decimal = field(repr=False)

    def __post_init__(self) -> None:
        _text(self.layer_id, "layer_id")
        _record_period(self.acquired_period, "acquired_period")
        original = money(self.original_cost, allow_zero=False)
        remaining = money(self._remaining_cost)
        if remaining > original:
            raise ValueError("remaining_cost cannot exceed original_cost")
        object.__setattr__(self, "original_cost", original)
        object.__setattr__(self, "_remaining_cost", remaining)

    @property
    def remaining_cost(self) -> Decimal:
        return self._remaining_cost

    @property
    def id(self) -> str:
        return self.layer_id

    def _consumed(self, amount: Decimal) -> InventoryLayer:
        return replace(self, _remaining_cost=self.remaining_cost - amount)


@dataclass(frozen=True, slots=True)
class PPEAsset:
    """Straight-line PP&E schedule for one asset."""

    asset_id: str
    entity_id: str
    placed_in_service_period: int
    gross_cost: Decimal
    residual_value: Decimal
    useful_life_periods: int
    _accumulated_depreciation: Decimal = field(repr=False)

    def __post_init__(self) -> None:
        _text(self.asset_id, "asset_id")
        _text(self.entity_id, "entity_id")
        _record_period(self.placed_in_service_period, "placed_in_service_period")
        if (
            isinstance(self.useful_life_periods, bool)
            or not isinstance(self.useful_life_periods, int)
            or self.useful_life_periods <= 0
        ):
            raise ValueError("useful_life_periods must be > 0")
        gross = money(self.gross_cost, allow_zero=False)
        residual = money(self.residual_value)
        accumulated = money(self._accumulated_depreciation)
        if residual >= gross:
            raise ValueError("residual_value must be less than gross_cost")
        if accumulated > gross - residual:
            raise ValueError("accumulated_depreciation exceeds the depreciable amount")
        object.__setattr__(self, "gross_cost", gross)
        object.__setattr__(self, "residual_value", residual)
        object.__setattr__(self, "_accumulated_depreciation", accumulated)

    @property
    def accumulated_depreciation(self) -> Decimal:
        return self._accumulated_depreciation

    @property
    def id(self) -> str:
        return self.asset_id

    @property
    def depreciable_amount(self) -> Decimal:
        return self.gross_cost - self.residual_value

    def _depreciated(self, amount: Decimal) -> PPEAsset:
        return replace(self, _accumulated_depreciation=self.accumulated_depreciation + amount)

    def _target_accumulation(self, period: int) -> Decimal:
        first_operating_period = max(1, self.placed_in_service_period)
        elapsed = min(
            max(period - first_operating_period + 1, 0),
            self.useful_life_periods,
        )
        if elapsed == self.useful_life_periods:
            return self.depreciable_amount
        regular = money(self.depreciable_amount / Decimal(self.useful_life_periods))
        return regular * Decimal(elapsed)


@dataclass(frozen=True, slots=True)
class DebtTranche:
    """Fixed-rate par debt with a ledger-controlled outstanding principal."""

    tranche_id: str
    entity_id: str
    issued_period: int
    maturity_period: int
    original_principal: Decimal
    _principal_outstanding: Decimal = field(repr=False)
    annual_rate: Decimal = field(repr=False)

    def __post_init__(self) -> None:
        _text(self.tranche_id, "tranche_id")
        _text(self.entity_id, "entity_id")
        _record_period(self.issued_period, "issued_period")
        _record_period(self.maturity_period, "maturity_period")
        if self.maturity_period <= self.issued_period:
            raise ValueError("maturity_period must be > issued_period")
        principal = money(self.original_principal, allow_zero=False)
        outstanding = money(self._principal_outstanding)
        if outstanding > principal:
            raise ValueError("principal_outstanding cannot exceed original_principal")
        rate = _rate(self.annual_rate)
        object.__setattr__(self, "original_principal", principal)
        object.__setattr__(self, "_principal_outstanding", outstanding)
        object.__setattr__(self, "annual_rate", rate)

    @property
    def principal_outstanding(self) -> Decimal:
        return self._principal_outstanding

    @property
    def id(self) -> str:
        return self.tranche_id

    def _repaid(self, amount: Decimal) -> DebtTranche:
        return replace(self, _principal_outstanding=self.principal_outstanding - amount)


@dataclass(frozen=True, slots=True)
class AccruedInterestSchedule:
    """Interest accrued and paid for one tranche in one quarter."""

    tranche_id: str
    period: int
    opening_principal: Decimal
    accrued_amount: Decimal
    paid_amount: Decimal
    ending_principal: Decimal

    def __post_init__(self) -> None:
        _text(self.tranche_id, "tranche_id")
        _period(self.period, "period")
        object.__setattr__(self, "opening_principal", money(self.opening_principal))
        object.__setattr__(self, "accrued_amount", money(self.accrued_amount))
        object.__setattr__(self, "paid_amount", money(self.paid_amount))
        object.__setattr__(self, "ending_principal", money(self.ending_principal))
        if self.paid_amount > self.accrued_amount:
            raise ValueError("paid interest cannot exceed accrued interest")

    @property
    def unpaid_amount(self) -> Decimal:
        return self.accrued_amount - self.paid_amount


def invoice_aging(
    invoices: dict[str, ReceivableInvoice] | dict[str, PayableInvoice],
    current_period: int,
) -> AgingBuckets:
    """Derive outstanding aging buckets without mutating the invoice schedule."""
    _period(current_period, "current_period")
    buckets: AgingBuckets = {
        "current": ZERO,
        "1-period-past-due": ZERO,
        "2-periods-past-due": ZERO,
        "3+-periods-past-due": ZERO,
    }
    for invoice in invoices.values():
        amount = invoice.outstanding_amount
        if invoice.due_period >= current_period:
            buckets["current"] += amount
        elif current_period - invoice.due_period == 1:
            buckets["1-period-past-due"] += amount
        elif current_period - invoice.due_period == 2:
            buckets["2-periods-past-due"] += amount
        else:
            buckets["3+-periods-past-due"] += amount
    return buckets


class OperationalBook:
    """Coordinate operational schedules and their authoritative general ledger."""

    def __init__(
        self,
        ledger: L.Ledger,
        *,
        receivables: RecordInput[ReceivableInvoice] = (),
        payables: RecordInput[PayableInvoice] = (),
        inventory_layers: RecordInput[InventoryLayer] = (),
        ppe_assets: RecordInput[PPEAsset] = (),
        debt_tranches: RecordInput[DebtTranche] = (),
        accrued_interest: Mapping[tuple[str, int], AccruedInterestSchedule]
        | Iterable[AccruedInterestSchedule] = (),
    ) -> None:
        self.ledger = ledger
        self.receivables = _copy_records(receivables, ReceivableInvoice, "invoice_id")
        self.payables = _copy_records(payables, PayableInvoice, "invoice_id")
        self.inventory_layers = _copy_records(inventory_layers, InventoryLayer, "layer_id")
        self.ppe_assets = _copy_records(ppe_assets, PPEAsset, "asset_id")
        self.debt_tranches = _copy_records(debt_tranches, DebtTranche, "tranche_id")
        self.accrued_interest = _copy_interest_schedules(accrued_interest)
        self._entry_sequence = self._last_entry_sequence()
        self._require_valid()

    def _last_entry_sequence(self) -> int:
        sequences = []
        for entry in self.ledger.entries:
            parts = entry.entry_id.split("-", 2)
            if len(parts) == 3 and parts[0] == "m41" and parts[1].isdigit():
                sequences.append(int(parts[1]))
        return max(sequences, default=0)

    def _post(
        self,
        event: str,
        amount: Decimal,
        period: int,
        event_time: str,
        posted_at: str,
    ) -> None:
        _period(period, "period")
        if amount <= ZERO:
            raise ValueError("a posted operational amount must be positive")
        candidate = self._entry_sequence + 1
        entry_id = f"m41-{candidate:08d}-{event}"
        self.ledger.post(
            L.event(
                entry_id,
                self.ledger.entity_id,
                period,
                event,
                amount,
                event_time,
                posted_at,
            )
        )
        self._entry_sequence = candidate

    def _require_cash(self, amount: Decimal) -> None:
        if self.ledger.balances()["cash"] < amount:
            raise ValueError("insufficient cash for operation")

    def _require_entity(self, entity_id: str) -> None:
        if entity_id != self.ledger.entity_id:
            raise ValueError("subledger record belongs to another entity")

    def _require_valid(self) -> None:
        failures = self.validate_reconciliations()
        if failures:
            raise ValueError(f"invalid operational book: {sorted(failures)}")

    def validate_reconciliations(self) -> set[str]:
        """Return every broken M4.1 reconciliation rule; valid books return an empty set."""
        failures: set[str] = set()
        balances = self.ledger.balances()

        try:
            _unique_ids(self.receivables, "id")
            receivable_invoices = tuple(self.receivables.values())
            if any(
                invoice.entity_id != self.ledger.entity_id
                or isinstance(invoice.issued_period, bool)
                or not isinstance(invoice.issued_period, int)
                or invoice.issued_period < 0
                or isinstance(invoice.due_period, bool)
                or not isinstance(invoice.due_period, int)
                or invoice.due_period < invoice.issued_period
                or invoice.original_amount <= ZERO
                or invoice.outstanding_amount < ZERO
                or invoice.outstanding_amount > invoice.original_amount
                for invoice in receivable_invoices
            ):
                raise ValueError("invalid receivable")
            outstanding = sum((invoice.outstanding_amount for invoice in receivable_invoices), ZERO)
            if outstanding != balances["ar"]:
                raise ValueError("receivable control mismatch")
        except (ArithmeticError, AttributeError, TypeError, ValueError):
            failures.add(AR_RULE)

        try:
            _unique_ids(self.payables, "id")
            payable_invoices = tuple(self.payables.values())
            if any(
                invoice.entity_id != self.ledger.entity_id
                or isinstance(invoice.issued_period, bool)
                or not isinstance(invoice.issued_period, int)
                or invoice.issued_period < 0
                or isinstance(invoice.due_period, bool)
                or not isinstance(invoice.due_period, int)
                or invoice.due_period < invoice.issued_period
                or invoice.original_amount <= ZERO
                or invoice.outstanding_amount < ZERO
                or invoice.outstanding_amount > invoice.original_amount
                for invoice in payable_invoices
            ):
                raise ValueError("invalid payable")
            outstanding = sum((invoice.outstanding_amount for invoice in payable_invoices), ZERO)
            if outstanding != balances["ap"]:
                raise ValueError("payable control mismatch")
        except (ArithmeticError, AttributeError, TypeError, ValueError):
            failures.add(AP_RULE)

        try:
            _unique_ids(self.inventory_layers, "id")
            layers = tuple(self.inventory_layers.values())
            if any(
                isinstance(layer.acquired_period, bool)
                or not isinstance(layer.acquired_period, int)
                or layer.acquired_period < 0
                or layer.original_cost <= ZERO
                or layer.remaining_cost < ZERO
                or layer.remaining_cost > layer.original_cost
                for layer in layers
            ):
                raise ValueError("invalid inventory layer")
            remaining = sum((layer.remaining_cost for layer in layers), ZERO)
            if remaining != balances["inventory"]:
                raise ValueError("inventory control mismatch")
        except (ArithmeticError, AttributeError, TypeError, ValueError):
            failures.add(INVENTORY_RULE)

        try:
            _unique_ids(self.ppe_assets, "id")
            assets = tuple(self.ppe_assets.values())
            if any(
                asset.entity_id != self.ledger.entity_id
                or isinstance(asset.placed_in_service_period, bool)
                or not isinstance(asset.placed_in_service_period, int)
                or asset.placed_in_service_period < 0
                or asset.gross_cost <= ZERO
                or asset.residual_value < ZERO
                or asset.residual_value >= asset.gross_cost
                or isinstance(asset.useful_life_periods, bool)
                or not isinstance(asset.useful_life_periods, int)
                or asset.useful_life_periods <= 0
                or asset.accumulated_depreciation < ZERO
                or asset.accumulated_depreciation > asset.depreciable_amount
                for asset in assets
            ):
                raise ValueError("invalid PP&E asset")
            gross = sum((asset.gross_cost for asset in assets), ZERO)
            accumulated = sum((asset.accumulated_depreciation for asset in assets), ZERO)
            if gross != balances["ppe"] or accumulated != balances["acc_dep"]:
                raise ValueError("PP&E control mismatch")
        except (ArithmeticError, AttributeError, TypeError, ValueError):
            failures.add(PPE_RULE)

        try:
            _unique_ids(self.debt_tranches, "id")
            tranches = tuple(self.debt_tranches.values())
            principal = ZERO
            interest_payable = ZERO
            expected_schedules: set[tuple[str, int]] = set()
            for tranche in tranches:
                principal += tranche.principal_outstanding
                if (
                    tranche.entity_id != self.ledger.entity_id
                    or isinstance(tranche.issued_period, bool)
                    or not isinstance(tranche.issued_period, int)
                    or tranche.issued_period < 0
                    or isinstance(tranche.maturity_period, bool)
                    or not isinstance(tranche.maturity_period, int)
                    or tranche.maturity_period <= tranche.issued_period
                    or tranche.original_principal <= ZERO
                    or tranche.principal_outstanding < ZERO
                    or tranche.principal_outstanding > tranche.original_principal
                    or not tranche.annual_rate.is_finite()
                    or tranche.annual_rate < ZERO
                ):
                    raise ValueError("invalid debt tranche")
                tranche_schedules = sorted(
                    period
                    for scheduled_tranche_id, period in self.accrued_interest
                    if scheduled_tranche_id == tranche.tranche_id
                )
                for period in tranche_schedules:
                    key = (tranche.tranche_id, period)
                    if key in expected_schedules:
                        raise ValueError("duplicate interest schedule")
                    expected_schedules.add(key)
                    schedule = self.accrued_interest[key]
                    if (
                        schedule.tranche_id != tranche.tranche_id
                        or schedule.period != period
                        or schedule.accrued_amount < ZERO
                        or schedule.paid_amount < ZERO
                        or schedule.paid_amount > schedule.accrued_amount
                        or schedule.opening_principal < ZERO
                        or schedule.opening_principal > tranche.original_principal
                        or schedule.ending_principal < tranche.principal_outstanding
                        or schedule.ending_principal > tranche.original_principal
                        or schedule.period <= tranche.issued_period
                        or schedule.period > tranche.maturity_period
                    ):
                        raise ValueError("invalid accrued-interest schedule")
                    expected_interest = money(
                        schedule.opening_principal * tranche.annual_rate / Decimal(4)
                    )
                    if schedule.accrued_amount != expected_interest:
                        raise ValueError("interest schedule does not match fixed-rate formula")
                    prior = self.accrued_interest.get((tranche.tranche_id, period - 1))
                    expected_opening = (
                        tranche.original_principal
                        if period == tranche.issued_period + 1 and prior is None
                        else prior.ending_principal
                        if prior is not None and period > tranche.issued_period + 1
                        else None
                    )
                    if expected_opening is None or schedule.opening_principal != expected_opening:
                        raise ValueError("non-contiguous interest schedule")
                    interest_payable += schedule.unpaid_amount
            if expected_schedules != set(self.accrued_interest):
                raise ValueError("interest schedule references an unknown tranche")
            if principal != balances["debt"] or interest_payable != balances["interest_payable"]:
                raise ValueError("debt or interest-payable control mismatch")
        except (ArithmeticError, AttributeError, TypeError, ValueError):
            failures.add(DEBT_RULE)

        return failures

    def receivables_aging(self, current_period: int) -> AgingBuckets:
        self._require_valid()
        return invoice_aging(self.receivables, current_period)

    def payables_aging(self, current_period: int) -> AgingBuckets:
        self._require_valid()
        return invoice_aging(self.payables, current_period)

    def issue_receivable(
        self,
        invoice_id: str,
        entity_id: str,
        issued_period: int,
        due_period: int,
        amount: DecimalSource,
        event_time: str,
        posted_at: str,
    ) -> ReceivableInvoice:
        self._require_entity(entity_id)
        _period(issued_period, "issued_period")
        self._require_valid()
        if invoice_id in self.receivables:
            raise ValueError(f"duplicate receivable invoice {invoice_id}")
        posted_amount = money(amount, allow_zero=False)
        invoice = ReceivableInvoice(
            invoice_id,
            entity_id,
            issued_period,
            due_period,
            posted_amount,
            posted_amount,
        )
        self._post("sale_on_credit", posted_amount, issued_period, event_time, posted_at)
        self.receivables[invoice_id] = invoice
        return invoice

    def collect_receivable(
        self,
        invoice_id: str,
        amount: DecimalSource,
        period: int,
        event_time: str,
        posted_at: str,
    ) -> Decimal:
        _period(period, "period")
        self._require_valid()
        invoice = self.receivables.get(invoice_id)
        if invoice is None:
            raise KeyError(f"unknown receivable invoice {invoice_id}")
        collected = money(amount, allow_zero=False)
        if collected > invoice.outstanding_amount:
            raise ValueError("collection exceeds outstanding receivable")
        if period < invoice.issued_period:
            raise ValueError("collection period precedes invoice issue")
        self._post("collect_ar", collected, period, event_time, posted_at)
        self.receivables[invoice_id] = invoice._collected(collected)
        return collected

    def issue_inventory_payable(
        self,
        invoice_id: str,
        layer_id: str,
        entity_id: str,
        issued_period: int,
        due_period: int,
        amount: DecimalSource,
        event_time: str,
        posted_at: str,
    ) -> PayableInvoice:
        self._require_entity(entity_id)
        _period(issued_period, "issued_period")
        self._require_valid()
        if invoice_id in self.payables:
            raise ValueError(f"duplicate payable invoice {invoice_id}")
        if layer_id in self.inventory_layers:
            raise ValueError(f"duplicate inventory layer {layer_id}")
        payable_amount = money(amount, allow_zero=False)
        payable = PayableInvoice(
            invoice_id,
            entity_id,
            issued_period,
            due_period,
            payable_amount,
            payable_amount,
        )
        layer = InventoryLayer(layer_id, issued_period, payable_amount, payable_amount)
        self._post("buy_inventory_on_credit", payable_amount, issued_period, event_time, posted_at)
        self.payables[invoice_id] = payable
        self.inventory_layers[layer_id] = layer
        return payable

    def pay_payable(
        self,
        invoice_id: str,
        amount: DecimalSource,
        period: int,
        event_time: str,
        posted_at: str,
    ) -> Decimal:
        _period(period, "period")
        self._require_valid()
        payable = self.payables.get(invoice_id)
        if payable is None:
            raise KeyError(f"unknown payable invoice {invoice_id}")
        paid = money(amount, allow_zero=False)
        if paid > payable.outstanding_amount:
            raise ValueError("payment exceeds outstanding payable")
        if period < payable.issued_period:
            raise ValueError("payment period precedes payable issue")
        self._require_cash(paid)
        self._post("pay_ap", paid, period, event_time, posted_at)
        self.payables[invoice_id] = payable._paid(paid)
        return paid

    def _fifo_plan(self, cost_amount: Decimal) -> tuple[tuple[str, Decimal], ...]:
        remaining = cost_amount
        plan: list[tuple[str, Decimal]] = []
        layers = sorted(self.inventory_layers.values(), key=lambda layer: layer.acquired_period)
        for layer in layers:
            if remaining == ZERO:
                break
            consumed = min(layer.remaining_cost, remaining)
            if consumed > ZERO:
                plan.append((layer.layer_id, consumed))
                remaining -= consumed
        if remaining != ZERO:
            raise ValueError("consumption exceeds available inventory")
        return tuple(plan)

    def consume_inventory(
        self,
        cost_amount: DecimalSource,
        period: int,
        event_time: str,
        posted_at: str,
    ) -> Decimal:
        _period(period, "period")
        self._require_valid()
        consumed = money(cost_amount, allow_zero=False)
        plan = self._fifo_plan(consumed)
        self._post("ship_goods", consumed, period, event_time, posted_at)
        for layer_id, amount in plan:
            self.inventory_layers[layer_id] = self.inventory_layers[layer_id]._consumed(amount)
        return consumed

    def acquire_ppe_asset(
        self,
        asset_id: str,
        entity_id: str,
        placed_in_service_period: int,
        gross_cost: DecimalSource,
        residual_value: DecimalSource,
        useful_life_periods: int,
        event_time: str,
        posted_at: str,
    ) -> PPEAsset:
        self._require_entity(entity_id)
        _period(placed_in_service_period, "placed_in_service_period")
        self._require_valid()
        if asset_id in self.ppe_assets:
            raise ValueError(f"duplicate PP&E asset {asset_id}")
        cost = money(gross_cost, allow_zero=False)
        asset = PPEAsset(
            asset_id,
            entity_id,
            placed_in_service_period,
            cost,
            money(residual_value),
            useful_life_periods,
            ZERO,
        )
        self._require_cash(cost)
        self._post("capex", cost, placed_in_service_period, event_time, posted_at)
        self.ppe_assets[asset_id] = asset
        return asset

    def depreciate_ppe(
        self,
        period: int,
        event_time: str,
        posted_at: str,
    ) -> Decimal:
        self._require_valid()
        _period(period, "period")
        allocations: list[tuple[str, Decimal]] = []
        for asset_id, asset in self.ppe_assets.items():
            if period < asset.placed_in_service_period:
                continue
            allocation = asset._target_accumulation(period) - asset.accumulated_depreciation
            if allocation < ZERO:
                raise ValueError("PP&E schedule would reverse accumulated depreciation")
            if allocation > ZERO:
                allocations.append((asset_id, allocation))
        total = sum((amount for _, amount in allocations), ZERO)
        if total == ZERO:
            return ZERO
        self._post("depreciate", total, period, event_time, posted_at)
        for asset_id, amount in allocations:
            self.ppe_assets[asset_id] = self.ppe_assets[asset_id]._depreciated(amount)
        return total

    def borrow_debt(
        self,
        tranche_id: str,
        entity_id: str,
        issued_period: int,
        maturity_period: int,
        principal: DecimalSource,
        annual_rate: DecimalSource,
        event_time: str,
        posted_at: str,
    ) -> DebtTranche:
        self._require_entity(entity_id)
        _period(issued_period, "issued_period")
        self._require_valid()
        if tranche_id in self.debt_tranches:
            raise ValueError(f"duplicate debt tranche {tranche_id}")
        amount = money(principal, allow_zero=False)
        tranche = DebtTranche(
            tranche_id,
            entity_id,
            issued_period,
            maturity_period,
            amount,
            amount,
            _rate(annual_rate),
        )
        self._post("borrow", amount, issued_period, event_time, posted_at)
        self.debt_tranches[tranche_id] = tranche
        return tranche

    def repay_debt(
        self,
        tranche_id: str,
        amount: DecimalSource,
        period: int,
        event_time: str,
        posted_at: str,
    ) -> Decimal:
        _period(period, "period")
        self._require_valid()
        tranche = self.debt_tranches.get(tranche_id)
        if tranche is None:
            raise KeyError(f"unknown debt tranche {tranche_id}")
        principal = money(amount, allow_zero=False)
        if principal > tranche.principal_outstanding:
            raise ValueError("repayment exceeds outstanding principal")
        self._require_cash(principal)
        self._post("repay", principal, period, event_time, posted_at)
        self.debt_tranches[tranche_id] = tranche._repaid(principal)
        schedule = self.accrued_interest.get((tranche_id, period))
        if schedule is not None:
            ending = self.debt_tranches[tranche_id].principal_outstanding
            self.accrued_interest[(tranche_id, period)] = replace(schedule, ending_principal=ending)
        return principal

    def accrue_interest(
        self,
        tranche_id: str,
        period: int,
        event_time: str,
        posted_at: str,
    ) -> Decimal:
        _period(period, "period")
        self._require_valid()
        tranche = self.debt_tranches.get(tranche_id)
        if tranche is None:
            raise KeyError(f"unknown debt tranche {tranche_id}")
        if not tranche.issued_period < period <= tranche.maturity_period:
            raise ValueError("interest must accrue after issue and no later than maturity")
        key = (tranche_id, period)
        if key in self.accrued_interest:
            raise ValueError(f"interest already accrued for {tranche_id} in period {period}")
        prior = self.accrued_interest.get((tranche_id, period - 1))
        if period == tranche.issued_period + 1:
            opening = tranche.original_principal
        elif prior is not None:
            opening = prior.ending_principal
        else:
            raise ValueError("quarterly interest schedule is missing an earlier period")
        accrued = money(opening * tranche.annual_rate / Decimal(4))
        schedule = AccruedInterestSchedule(
            tranche_id,
            period,
            opening,
            accrued,
            ZERO,
            tranche.principal_outstanding,
        )
        if accrued > ZERO:
            self._post("accrue_interest", accrued, period, event_time, posted_at)
        self.accrued_interest[key] = schedule
        return accrued

    def pay_accrued_interest(
        self,
        amount: DecimalSource,
        period: int,
        event_time: str,
        posted_at: str,
        *,
        tranche_id: str | None = None,
    ) -> Decimal:
        _period(period, "period")
        self._require_valid()
        payment = money(amount, allow_zero=False)
        schedules = sorted(
            (
                schedule
                for (schedule_tranche_id, _), schedule in self.accrued_interest.items()
                if (tranche_id is None or schedule_tranche_id == tranche_id)
                and schedule.period <= period
                and schedule.unpaid_amount > ZERO
            ),
            key=lambda schedule: (schedule.period, schedule.tranche_id),
        )
        available = sum((schedule.unpaid_amount for schedule in schedules), ZERO)
        if payment > available:
            raise ValueError("payment exceeds accrued unpaid interest")
        self._require_cash(payment)
        remaining = payment
        plan: list[tuple[tuple[str, int], Decimal]] = []
        for schedule in schedules:
            paid = min(schedule.unpaid_amount, remaining)
            if paid > ZERO:
                key = (schedule.tranche_id, schedule.period)
                plan.append((key, paid))
                remaining -= paid
        if remaining != ZERO:
            raise ValueError("interest-payment split does not reconcile")
        self._post("pay_accrued_interest", payment, period, event_time, posted_at)
        for key, paid in plan:
            schedule = self.accrued_interest[key]
            self.accrued_interest[key] = replace(schedule, paid_amount=schedule.paid_amount + paid)
        return payment
