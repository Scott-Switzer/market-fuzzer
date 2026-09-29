"""Business preconditions for generated M5 event sequences.

This module is the *generator's* bookkeeping. Its only job is to make sure a
generated sequence is valid business-wise: it never collects a receivable that
does not exist, never pays more than is outstanding, never consumes inventory
the company does not hold, never repays principal beyond the tranche, never
repurchases more shares than are outstanding, never pays a dividend the company
cannot fund, and never posts into a closed period.

It deliberately derives **no** accounting amount that the FWF kernel also
derives. Depreciation charges, interest accruals, FIFO layer splits, and period
close transfers are each derived independently by the side that posts them:

* the FWF kernel derives them from its own subledger schedules;
* the oracle derives them from the business events it received
  (:mod:`tests.m5.oracle_side`).

Keeping this module free of derivations means the oracle's independent
arithmetic is never a copy of the generator's.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value: Decimal | int | str) -> Decimal:
    """Return the canonical cent-quantized monetary amount."""
    return Decimal(value).quantize(CENT)


@dataclass
class Receivable:
    invoice_id: str
    due_period: int
    outstanding: Decimal


@dataclass
class Payable:
    invoice_id: str
    due_period: int
    outstanding: Decimal


@dataclass
class Layer:
    layer_id: str
    acquired_period: int
    remaining: Decimal


@dataclass
class Tranche:
    """A fixed-rate par debt tranche, tracked for precondition purposes.

    ``original`` is retained because a tranche's first quarterly charge accrues
    on its original principal, not on whatever principal happens to be
    outstanding when the schedule runs.
    """

    tranche_id: str
    issued_period: int
    maturity_period: int
    original: Decimal
    outstanding: Decimal
    annual_rate: Decimal
    accrued: dict[int, Decimal] = field(default_factory=dict)
    paid: dict[int, Decimal] = field(default_factory=dict)
    ending: dict[int, Decimal] = field(default_factory=dict)


@dataclass
class ShareState:
    issued: int = 0
    treasury: int = 0
    par_value: Decimal = Decimal("0.01")

    @property
    def outstanding(self) -> int:
        return self.issued - self.treasury

    def common_stock(self) -> Decimal:
        return money(Decimal(self.issued) * self.par_value)


@dataclass
class BusinessState:
    """Precondition bookkeeping for one generated sequence."""

    entity_id: str
    cash: Decimal = ZERO
    receivables: dict[str, Receivable] = field(default_factory=dict)
    payables: dict[str, Payable] = field(default_factory=dict)
    layers: deque[Layer] = field(default_factory=deque)
    tranches: dict[str, Tranche] = field(default_factory=dict)
    shares: ShareState = field(default_factory=ShareState)
    tax_payable: Decimal = ZERO
    interest_payable: Decimal = ZERO
    inventory_available: Decimal = ZERO
    retained_earnings: Decimal = ZERO
    closed_periods: set[int] = field(default_factory=set)
    period: int = 0
    _next_invoice: int = 0
    _next_layer: int = 0
    _next_asset: int = 0
    _next_tranche: int = 0

    # ------------------------------------------------------------- accessors
    def open_receivables(self) -> list[Receivable]:
        return [item for item in self.receivables.values() if item.outstanding > ZERO]

    def open_payables(self) -> list[Payable]:
        return [item for item in self.payables.values() if item.outstanding > ZERO]

    def active_tranches(self) -> list[Tranche]:
        return [item for item in self.tranches.values() if item.outstanding > ZERO]

    def available_interest(self) -> Decimal:
        return money(self.interest_payable)

    def can_pay(self, amount: Decimal) -> bool:
        return amount <= self.cash

    def is_open(self, period: int) -> bool:
        return period not in self.closed_periods

    # -------------------------------------------------------------- id mint
    def invoice_id(self) -> str:
        self._next_invoice += 1
        return f"m5-ar-{self._next_invoice:04d}"

    def payable_id(self) -> str:
        self._next_invoice += 1
        return f"m5-ap-{self._next_invoice:04d}"

    def layer_id(self) -> str:
        self._next_layer += 1
        return f"m5-inv-{self._next_layer:04d}"

    def asset_id(self) -> str:
        self._next_asset += 1
        return f"m5-ppe-{self._next_asset:04d}"

    def tranche_id(self) -> str:
        self._next_tranche += 1
        return f"m5-debt-{self._next_tranche:04d}"
