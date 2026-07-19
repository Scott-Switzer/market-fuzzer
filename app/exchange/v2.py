"""Immutable, deterministic event-kernel primitives for the sealed evaluator.

This module deliberately does not route the existing product through a second
matching engine.  It establishes the auditable command/event contract that the
V2 matching, risk, settlement, and sealed-evaluation services will share.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from heapq import heappop, heappush
from typing import Any


class ExchangeValidationError(ValueError):
    """A typed, client-safe command validation error."""


class OrderRejectedError(ExchangeValidationError):
    """Raised when an order cannot be admitted to the kernel."""


class EventKindV2(StrEnum):
    ORDER_ACKNOWLEDGED = "order_acknowledged"
    ORDER_REJECTED = "order_rejected"
    COMMAND_ACCEPTED = "command_accepted"
    ORDER_CANCELLED = "order_cancelled"
    TRADE_EXECUTED = "trade_executed"


class SideV2(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> SideV2:
        return SideV2.SELL if self == SideV2.BUY else SideV2.BUY


class OrderTypeV2(StrEnum):
    LIMIT = "limit"
    MARKET = "market"


class TimeInForceV2(StrEnum):
    DAY = "day"
    IOC = "ioc"
    FOK = "fok"


class SelfTradePreventionV2(StrEnum):
    CANCEL_TAKER = "cancel_taker"
    CANCEL_MAKER = "cancel_maker"
    DECREMENT_AND_CANCEL = "decrement_and_cancel"


def _require_nonempty(value: str, field_name: str) -> None:
    if not value:
        raise ExchangeValidationError(f"{field_name} must be non-empty")


@dataclass(frozen=True, slots=True)
class OrderCommandV2:
    command_id: str
    order_id: str
    account_id: str
    instrument_id: str
    side: SideV2
    order_type: OrderTypeV2
    quantity: int
    exchange_time_ns: int
    venue_sequence: int
    price_ticks: int | None = None
    time_in_force: TimeInForceV2 = TimeInForceV2.DAY
    self_trade_prevention: SelfTradePreventionV2 = SelfTradePreventionV2.CANCEL_TAKER

    def __post_init__(self) -> None:
        for value, name in (
            (self.command_id, "command_id"),
            (self.order_id, "order_id"),
            (self.account_id, "account_id"),
            (self.instrument_id, "instrument_id"),
        ):
            _require_nonempty(value, name)
        if self.quantity <= 0:
            raise ExchangeValidationError("quantity must be positive")
        if self.exchange_time_ns < 0 or self.venue_sequence < 0:
            raise ExchangeValidationError("time and venue sequence must be non-negative")
        if self.order_type == OrderTypeV2.LIMIT and (self.price_ticks is None or self.price_ticks <= 0):
            raise ExchangeValidationError("limit order requires positive price_ticks")
        if self.order_type == OrderTypeV2.MARKET and self.price_ticks is not None:
            raise ExchangeValidationError("market order must not set price_ticks")
        if self.time_in_force == TimeInForceV2.FOK and self.order_type == OrderTypeV2.MARKET:
            raise ExchangeValidationError("FOK market orders require an explicit price protection limit")


@dataclass(frozen=True, slots=True)
class OrderEventV2:
    event_id: str
    kind: EventKindV2
    exchange_time_ns: int
    venue_sequence: int
    event_priority: int
    command_id: str
    order_id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, name in (
            (self.event_id, "event_id"),
            (self.command_id, "command_id"),
            (self.order_id, "order_id"),
        ):
            _require_nonempty(value, name)
        if min(self.exchange_time_ns, self.venue_sequence, self.event_priority) < 0:
            raise ExchangeValidationError("event ordering fields must be non-negative")

    @property
    def ordering_key(self) -> tuple[int, int, int, str]:
        return (self.exchange_time_ns, self.venue_sequence, self.event_priority, self.event_id)

    def canonical_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data


@dataclass(frozen=True, slots=True)
class RunManifestV2:
    specification_digest: str
    strategy_artifact_digest: str
    generator_bundle_digest: str
    campaign_commitment: str
    seed_material_digest: str
    protocol_version: str = "event-kernel-v2"

    def __post_init__(self) -> None:
        for value, name in (
            (self.specification_digest, "specification_digest"),
            (self.strategy_artifact_digest, "strategy_artifact_digest"),
            (self.generator_bundle_digest, "generator_bundle_digest"),
            (self.campaign_commitment, "campaign_commitment"),
            (self.seed_material_digest, "seed_material_digest"),
            (self.protocol_version, "protocol_version"),
        ):
            _require_nonempty(value, name)

    def canonical_bytes(self) -> bytes:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()


class ImmutableEventLedgerV2:
    """Append-only, canonicalized event ledger with a reproducible digest."""

    def __init__(self, manifest: RunManifestV2) -> None:
        self.manifest = manifest
        self._events: list[OrderEventV2] = []
        self._event_ids: set[str] = set()

    def append(self, event: OrderEventV2) -> None:
        if event.event_id in self._event_ids:
            raise ExchangeValidationError(f"duplicate event_id {event.event_id}")
        if self._events and event.ordering_key < self._events[-1].ordering_key:
            raise ExchangeValidationError("events must be appended in total ordering")
        self._events.append(event)
        self._event_ids.add(event.event_id)

    @property
    def events(self) -> tuple[OrderEventV2, ...]:
        return tuple(self._events)

    def canonical_bytes(self) -> bytes:
        payload = {
            "manifest": asdict(self.manifest),
            "events": [event.canonical_dict() for event in self._events],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def replay(cls, manifest: RunManifestV2, events: tuple[OrderEventV2, ...]) -> ImmutableEventLedgerV2:
        ledger = cls(manifest)
        for event in events:
            ledger.append(event)
        return ledger


class DeterministicSchedulerV2:
    """Schedules events by the declared total order, never insertion order."""

    def __init__(self) -> None:
        self._queue: list[tuple[tuple[int, int, int, str], OrderEventV2]] = []

    def schedule(self, event: OrderEventV2) -> None:
        heappush(self._queue, (event.ordering_key, event))

    def drain(self) -> tuple[OrderEventV2, ...]:
        events: list[OrderEventV2] = []
        while self._queue:
            events.append(heappop(self._queue)[1])
        return tuple(events)


class EventKernelV2:
    """Deterministic command admission and provenance bridge for future matching."""

    def __init__(self, manifest: RunManifestV2) -> None:
        self.ledger = ImmutableEventLedgerV2(manifest)
        self._command_ids: set[str] = set()
        self._order_ids: set[str] = set()
        self._event_sequence = 0

    def admit(self, command: OrderCommandV2) -> OrderEventV2:
        if command.command_id in self._command_ids:
            raise OrderRejectedError(f"duplicate command_id {command.command_id}")
        self._command_ids.add(command.command_id)
        self._event_sequence += 1
        rejected = command.order_id in self._order_ids
        if not rejected:
            self._order_ids.add(command.order_id)
        kind = EventKindV2.ORDER_REJECTED if rejected else EventKindV2.ORDER_ACKNOWLEDGED
        event = OrderEventV2(
            event_id=f"evt-{self._event_sequence:020d}",
            kind=kind,
            exchange_time_ns=command.exchange_time_ns,
            venue_sequence=command.venue_sequence,
            event_priority=10,
            command_id=command.command_id,
            order_id=command.order_id,
            payload={"reason": "duplicate_order_id"} if rejected else {"accepted_quantity": command.quantity},
        )
        self.ledger.append(event)
        return event
