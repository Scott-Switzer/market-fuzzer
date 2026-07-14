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

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("order quantity must be positive")
        if self.order_type == OrderType.LIMIT and (self.price_ticks is None or self.price_ticks <= 0):
            raise ValueError("limit order requires positive price_ticks")
        if self.order_type == OrderType.MARKET and self.price_ticks is not None:
            raise ValueError("market order must not set price_ticks")
        if self.remaining is None:
            self.remaining = self.quantity

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
    maker_fee_cents: int = 0
    taker_fee_cents: int = 0

    def to_dict(self) -> dict:
        return asdict(self)
