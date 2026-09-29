"""Canonical M5 business-event model.

This is the only shared input to the differential oracle. It records business
facts and nothing else:

* it never imports the FWF accounting kernel;
* it never imports ``python-accounting``;
* it never carries a precomputed FWF journal line, debit, credit, or balance.

Both sides of the comparison receive the same :class:`BusinessEvent` and each
independently decides how to post it:

* :mod:`tests.m5.fwf_side` maps it onto FWF ``OperationalBook`` / ``EquityBook``
  / ``Ledger`` operations;
* :mod:`tests.m5.oracle_side` maps it onto ``python-accounting`` transactions.

Amounts are carried as exact cent-quantized decimal *strings* so no binary
float ever crosses the accounting boundary. Quantities (share counts) are
integers. Term fields (due periods, useful lives, annual rates) are the business
facts the postings depend on.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


class EventType(StrEnum):
    """Every business operation the M5 oracle attempts to support."""

    OPENING = "opening"
    CREDIT_REVENUE = "credit_revenue"
    AR_COLLECTION = "ar_collection"
    INVENTORY_PURCHASE = "inventory_purchase"
    AP_PAYMENT = "ap_payment"
    INVENTORY_CONSUMPTION = "inventory_consumption"
    PPE_ACQUISITION = "ppe_acquisition"
    DEPRECIATION = "depreciation"
    DEBT_BORROWING = "debt_borrowing"
    INTEREST_ACCRUAL = "interest_accrual"
    INTEREST_PAYMENT = "interest_payment"
    DEBT_REPAYMENT = "debt_repayment"
    SGA = "sga"
    TAX_ACCRUAL = "tax_accrual"
    TAX_PAYMENT = "tax_payment"
    SHARE_ISSUANCE = "share_issuance"
    SHARE_REPURCHASE = "share_repurchase"
    DIVIDENDS = "dividends"
    PERIOD_CLOSE = "period_close"


#: Ordered support list recorded in the oracle evidence artifact.
SUPPORTED_EVENT_TYPES: tuple[EventType, ...] = tuple(EventType)


def money(value: Decimal | int | str) -> str:
    """Return the canonical cent-quantized decimal string for an amount."""
    return str(Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class BusinessEvent:
    """One business fact, with no accounting-posting detail attached.

    Attributes:
        event_id: deterministic identity, unique within a sequence.
        entity_id: the reporting entity the event belongs to.
        period: the operating period; ``0`` is the opening period.
        event_type: which business operation occurred.
        amount: exact cent-quantized monetary amount, as a string.
        reference: the counterparty record the event refers to (invoice id,
            asset id, tranche id), or ``""`` when the event has none.
        due_period: contractual due period for receivable/payable events.
        quantity: an integer quantity (share count) for equity events.
        rate: an exact decimal rate string for debt events.
        useful_life: an integer period count for PP&E events.
        effective_date: ISO calendar date for equity events (``YYYY-MM-DD``).
    """

    event_id: str
    entity_id: str
    period: int
    event_type: EventType
    amount: str = "0.00"
    reference: str = ""
    due_period: int = 0
    quantity: int = 0
    rate: str = "0"
    useful_life: int = 0
    effective_date: str = ""

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must be a non-empty string")
        if not self.entity_id.strip():
            raise ValueError("entity_id must be a non-empty string")
        if self.period < 0:
            raise ValueError("period must be >= 0")
        # Re-quantize so an event can never carry a sub-cent monetary fact.
        if self.amount != money(self.amount):
            raise ValueError(f"{self.event_id}: amount is not cent-quantized")
        if self.event_type is EventType.OPENING and self.period != 0:
            raise ValueError("the opening event belongs to period 0")
        if self.event_type is not EventType.OPENING and self.period < 1:
            raise ValueError("operating events belong to period >= 1")
