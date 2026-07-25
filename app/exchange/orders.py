from __future__ import annotations

from dataclasses import dataclass
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
    display_quantity: int | None = None  # iceberg visible size
    hidden_quantity: int = 0
    priority_score: float = 0.0
    rested_step: int | None = None

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("order quantity must be positive")
        if self.order_type == OrderType.LIMIT and (self.price_ticks is None or self.price_ticks <= 0):
            raise ValueError("limit order requires positive price_ticks")
        if self.order_type == OrderType.MARKET and self.price_ticks is not None:
            raise ValueError("market order must not set price_ticks")
        if self.remaining is None:
            self.remaining = self.quantity
        if self.display_quantity is None:
            self.display_quantity = self.quantity
        else:
            self.display_quantity = max(1, min(int(self.display_quantity), int(self.quantity)))
        self.hidden_quantity = max(0, int(self.quantity) - int(self.display_quantity))
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

    @property
    def disclosed_size(self) -> int:
        return int(self.display_quantity or 0)

    @property
    def visible_remaining(self) -> int:
        rem = int(self.remaining or 0)
        disp = int(self.display_quantity or rem)
        return min(rem, disp)

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "agent_id": self.agent_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "submitted_step": self.submitted_step,
            "price_ticks": self.price_ticks,
            "remaining": self.remaining,
            "sequence": self.sequence,
            "market_event_time_ms": self.market_event_time_ms,
            "publication_time_ms": self.publication_time_ms,
            "observation_time_ms": self.observation_time_ms,
            "decision_time_ms": self.decision_time_ms,
            "submission_time_ms": self.submission_time_ms,
            "exchange_arrival_time_ms": self.exchange_arrival_time_ms,
            "acknowledgment_time_ms": self.acknowledgment_time_ms,
            "exchange_arrival_step": self.exchange_arrival_step,
            "displayed_quantity_ahead_at_entry": self.displayed_quantity_ahead_at_entry,
            "level_executed_quantity_at_entry": self.level_executed_quantity_at_entry,
            "rested_quantity_at_entry": self.rested_quantity_at_entry,
            "display_quantity": self.display_quantity,
            "hidden_quantity": self.hidden_quantity,
            "priority_score": self.priority_score,
            "rested_step": self.rested_step,
        }


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
    trade_toxicity_direction: int = 0  # +1 when taker hits ask / buys aggression
    fill_probability: float | None = None
    queue_position_score: float | None = None

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "price_ticks": self.price_ticks,
            "quantity": self.quantity,
            "buyer_id": self.buyer_id,
            "seller_id": self.seller_id,
            "maker_order_id": self.maker_order_id,
            "taker_order_id": self.taker_order_id,
            "step": self.step,
            "maker_id": self.maker_id,
            "taker_id": self.taker_id,
            "arrival_step": self.arrival_step,
            "fill_step": self.fill_step,
            "arrival_time_ms": self.arrival_time_ms,
            "fill_time_ms": self.fill_time_ms,
            "fill_sequence": self.fill_sequence,
            "maker_partial_fill_sequence": self.maker_partial_fill_sequence,
            "taker_partial_fill_sequence": self.taker_partial_fill_sequence,
            "maker_queue_ahead_at_entry": self.maker_queue_ahead_at_entry,
            "quantity_traded_at_level_before_fill": self.quantity_traded_at_level_before_fill,
            "maker_fee_cents": self.maker_fee_cents,
            "taker_fee_cents": self.taker_fee_cents,
            "trade_toxicity_direction": self.trade_toxicity_direction,
            "fill_probability": self.fill_probability,
            "queue_position_score": self.queue_position_score,
        }
