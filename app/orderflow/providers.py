from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal, Protocol

from app.exchange import CancelRequest, Exchange, Order, OrderType, Side
from app.schemas import WorldSpec

EventType = Literal["bid_limit", "ask_limit", "bid_cancel", "ask_cancel", "buy_market", "sell_market"]


@dataclass(frozen=True)
class OrderFlowAction:
    event_type: EventType
    symbol: str
    order: Order | None = None
    cancel: CancelRequest | None = None
    backoff_level: int = 0


class OrderFlowProvider(Protocol):
    account_ids: tuple[str, ...]

    def actions(
        self, step: int, symbol: str, exchange: Exchange, rng: random.Random
    ) -> list[OrderFlowAction]: ...


class RuleBasedProvider:
    """Compatibility provider: existing heterogeneous agents remain the order-flow source."""

    account_ids: tuple[str, ...] = ()

    def actions(
        self, step: int, symbol: str, exchange: Exchange, rng: random.Random
    ) -> list[OrderFlowAction]:
        return []


class QueueReactiveProvider:
    """Small, auditable queue-reactive provider; the exchange still owns all state transitions."""

    account_ids = ("queue-reactive-liquidity", "queue-reactive-flow")
    event_types: tuple[EventType, ...] = (
        "bid_limit",
        "ask_limit",
        "bid_cancel",
        "ask_cancel",
        "buy_market",
        "sell_market",
    )

    def __init__(self, spec: WorldSpec) -> None:
        self.spec = spec
        self.counter = 0
        defaults = {
            "bid_limit_intensity": 1.0,
            "ask_limit_intensity": 1.0,
            "bid_cancel_intensity": 0.35,
            "ask_cancel_intensity": 0.35,
            "buy_market_intensity": 0.30,
            "sell_market_intensity": 0.30,
            "base_order_size": 80.0,
            "flow_persistence": 0.25,
            "volatility_sensitivity": 0.20,
        }
        self.parameters = {**defaults, **spec.order_flow_parameters}
        self.last_event: dict[str, EventType | None] = {asset.ticker: None for asset in spec.assets}
        self.recent_signs: dict[str, list[int]] = {asset.ticker: [] for asset in spec.assets}
        self.recent_mids: dict[str, list[int]] = {asset.ticker: [] for asset in spec.assets}

    def _next_id(self) -> str:
        self.counter += 1
        return f"QRF-O{self.counter:09d}"

    def _state(self, symbol: str, exchange: Exchange) -> tuple[dict, float, float, float, float, int]:
        snapshot = exchange.books[symbol].snapshot(5)
        bid, ask = snapshot["best_bid_ticks"], snapshot["best_ask_ticks"]
        bid_depth = sum(level["quantity"] for level in snapshot["bids"])
        ask_depth = sum(level["quantity"] for level in snapshot["asks"])
        total = bid_depth + ask_depth
        best_depth = sum(level["quantity"] for level in snapshot["bids"][:1] + snapshot["asks"][:1])
        imbalance = (bid_depth - ask_depth) / total if total else 0.0
        spread = float(ask - bid) if bid is not None and ask is not None else 8.0
        signs = self.recent_signs[symbol][-8:]
        signed_flow = sum(signs) / len(signs) if signs else 0.0
        mids = self.recent_mids[symbol][-8:]
        volatility = 0.0
        if len(mids) > 2:
            changes = [abs(mids[i] / mids[i - 1] - 1) for i in range(1, len(mids)) if mids[i - 1]]
            volatility = sum(changes) / len(changes) if changes else 0.0
        return snapshot, spread, imbalance, signed_flow, volatility, best_depth

    def _weights(self, symbol: str, exchange: Exchange) -> tuple[list[float], int]:
        snapshot, spread, imbalance, signed_flow, volatility, best_depth = self._state(symbol, exchange)
        sparse_book = not snapshot["bids"] or not snapshot["asks"]
        backoff_level = 2 if sparse_book else 1 if abs(imbalance) > 0.8 else 0
        persistence = float(self.parameters["flow_persistence"])
        last = self.last_event[symbol]
        intervention = self.spec.interventions
        depth = intervention.displayed_depth_multiplier
        flow_state = signed_flow + volatility * 100 * float(self.parameters["volatility_sensitivity"])
        weights = [
            float(self.parameters["bid_limit_intensity"]) * depth * (1 - 0.35 * imbalance),
            float(self.parameters["ask_limit_intensity"]) * depth * (1 + 0.35 * imbalance),
            float(self.parameters["bid_cancel_intensity"]) / depth * (1 + max(0.0, imbalance)),
            float(self.parameters["ask_cancel_intensity"]) / depth * (1 + max(0.0, -imbalance)),
            float(self.parameters["buy_market_intensity"]) * (1 + max(0.0, flow_state)),
            float(self.parameters["sell_market_intensity"]) * (1 + max(0.0, -flow_state)),
        ]
        if last in self.event_types:
            weights[self.event_types.index(last)] *= 1 + persistence
        if spread > 6:
            weights[0] *= 1.4
            weights[1] *= 1.4
        if best_depth < float(self.parameters["base_order_size"]):
            weights[0] *= 1.25
            weights[1] *= 1.25
        return [max(0.001, value) for value in weights], backoff_level

    def _cancel(self, step: int, symbol: str, exchange: Exchange, side: Side) -> OrderFlowAction | None:
        book = exchange.books[symbol]
        candidates = [
            order
            for order in book.orders.values()
            if order.agent_id == self.account_ids[0] and order.side == side
        ]
        if not candidates:
            return None
        order = min(candidates, key=lambda value: value.sequence)
        event_type: EventType = "bid_cancel" if side == Side.BUY else "ask_cancel"
        return OrderFlowAction(event_type, symbol, cancel=CancelRequest(order.order_id, order.agent_id, step))

    def actions(
        self, step: int, symbol: str, exchange: Exchange, rng: random.Random
    ) -> list[OrderFlowAction]:
        snapshot, _, _, _, _, _ = self._state(symbol, exchange)
        bid, ask = snapshot["best_bid_ticks"], snapshot["best_ask_ticks"]
        reference = exchange.books[symbol].last_price_ticks or next(
            asset.initial_price_ticks for asset in self.spec.assets if asset.ticker == symbol
        )
        mid = round((bid + ask) / 2) if bid is not None and ask is not None else reference
        self.recent_mids[symbol].append(mid)
        weights, backoff = self._weights(symbol, exchange)
        event_type = rng.choices(self.event_types, weights=weights, k=1)[0]
        self.last_event[symbol] = event_type
        if event_type == "bid_cancel":
            action = self._cancel(step, symbol, exchange, Side.BUY)
            return [action] if action else self._limit(step, symbol, Side.BUY, mid, backoff)
        if event_type == "ask_cancel":
            action = self._cancel(step, symbol, exchange, Side.SELL)
            return [action] if action else self._limit(step, symbol, Side.SELL, mid, backoff)
        if event_type in {"bid_limit", "ask_limit"}:
            side = Side.BUY if event_type == "bid_limit" else Side.SELL
            return self._limit(step, symbol, side, mid, backoff)
        side = Side.BUY if event_type == "buy_market" else Side.SELL
        if (side == Side.BUY and ask is None) or (side == Side.SELL and bid is None):
            return self._limit(step, symbol, side, mid, backoff)
        quantity = max(self.spec.exchange.lot_size, int(float(self.parameters["base_order_size"]) / 2))
        quantity -= quantity % self.spec.exchange.lot_size
        order = Order(self._next_id(), self.account_ids[1], symbol, side, OrderType.MARKET, quantity, step)
        self.recent_signs[symbol].append(1 if side == Side.BUY else -1)
        return [OrderFlowAction(event_type, symbol, order=order, backoff_level=backoff)]

    def _limit(self, step: int, symbol: str, side: Side, mid: int, backoff: int) -> list[OrderFlowAction]:
        size = int(
            float(self.parameters["base_order_size"]) * self.spec.interventions.displayed_depth_multiplier
        )
        size = max(self.spec.exchange.lot_size, size - size % self.spec.exchange.lot_size)
        offset = 2 + backoff
        price = max(1, mid - offset if side == Side.BUY else mid + offset)
        event_type: EventType = "bid_limit" if side == Side.BUY else "ask_limit"
        order = Order(self._next_id(), self.account_ids[0], symbol, side, OrderType.LIMIT, size, step, price)
        return [OrderFlowAction(event_type, symbol, order=order, backoff_level=backoff)]
