from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    LIMIT = "limit"
    MARKET = "market"


@dataclass
class Order:
    order_id: str
    agent_id: str
    symbol: str
    side: Side
    order_type: OrderType
    quantity: int
    submitted_step: int
    price_ticks: int | None = None
    remaining: int | None = None
    sequence: int = 0
    market_event_time_ms: int | None = None
    publication_time_ms: int | None = None
    observation_time_ms: int | None = None
    decision_time_ms: int | None = None
    submission_time_ms: int | None = None
    exchange_arrival_time_ms: int | None = None
    acknowledgment_time_ms: int | None = None
    exchange_arrival_step: int | None = None
    displayed_quantity_ahead_at_entry: int | None = None
    level_executed_quantity_at_entry: int | None = None
    rested_quantity_at_entry: int = 0

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("order quantity must be positive")
        if self.order_type == OrderType.LIMIT and (self.price_ticks is None or self.price_ticks <= 0):
            raise ValueError("limit order requires positive price_ticks")
        if self.order_type == OrderType.MARKET and self.price_ticks is not None:
            raise ValueError("market order must not set price_ticks")
        if self.remaining is None:
            self.remaining = self.quantity
        ordered_times = [
            self.market_event_time_ms,
            self.publication_time_ms,
            self.observation_time_ms,
            self.decision_time_ms,
            self.submission_time_ms,
            self.exchange_arrival_time_ms,
            self.acknowledgment_time_ms,
        ]
        present_times = [value for value in ordered_times if value is not None]
        if present_times != sorted(present_times):
            raise ValueError("order lifecycle timestamps must be monotonic")

    def to_dict(self) -> dict:
        value = asdict(self)
        value["side"] = self.side.value
        value["order_type"] = self.order_type.value
        return value


@dataclass(frozen=True)
class CancelRequest:
    order_id: str
    agent_id: str
    submitted_step: int
    request_time_ms: int | None = None
    effective_step: int | None = None
    effective_time_ms: int | None = None


@dataclass(frozen=True)
class Trade:
    trade_id: str
    symbol: str
    price_ticks: int
    quantity: int
    buyer_id: str
    seller_id: str
    maker_order_id: str
    taker_order_id: str
    step: int
    maker_id: str = ""
    taker_id: str = ""
    arrival_step: int | None = None
    fill_step: int | None = None
    arrival_time_ms: int | None = None
    fill_time_ms: int | None = None
    fill_sequence: int = 0
    maker_partial_fill_sequence: int = 0
    taker_partial_fill_sequence: int = 0
    maker_queue_ahead_at_entry: int | None = None
    quantity_traded_at_level_before_fill: int | None = None
    maker_fee_cents: int = 0
    taker_fee_cents: int = 0

    def to_dict(self) -> dict:
        return asdict(self)
