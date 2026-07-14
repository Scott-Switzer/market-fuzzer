from __future__ import annotations

from collections import defaultdict, deque

from .orders import Order, OrderType, Side, Trade


class OrderBook:
    def __init__(self, symbol: str, tick_size_cents: int, lot_size: int) -> None:
        self.symbol, self.tick_size_cents, self.lot_size = symbol, tick_size_cents, lot_size
        self.orders: dict[str, Order] = {}
        self.bid_levels: dict[int, deque[str]] = defaultdict(deque)
        self.ask_levels: dict[int, deque[str]] = defaultdict(deque)
        self.seen_ids: set[str] = set()
        self.sequence = 0
        self.trade_sequence = 0
        self.last_price_ticks: int | None = None
        self.halted_until_step = -1

    def is_halted(self, step: int) -> bool:
        return step < self.halted_until_step

    def halt(self, current_step: int, halt_steps: int) -> None:
        self.halted_until_step = max(self.halted_until_step, current_step + halt_steps)

    @property
    def best_bid(self) -> int | None:
        return max(self.bid_levels) if self.bid_levels else None

    @property
    def best_ask(self) -> int | None:
        return min(self.ask_levels) if self.ask_levels else None

    def submit(self, order: Order, step: int) -> list[Trade]:
        self._validate(order)
        if self.is_halted(step):
            raise RuntimeError(f"{self.symbol} is halted until step {self.halted_until_step}")
        self.seen_ids.add(order.order_id)
        self.sequence += 1
        order.sequence = self.sequence
        trades = self._match(order, step)
        if order.remaining and order.order_type == OrderType.LIMIT:
            self.orders[order.order_id] = order
            levels = self.bid_levels if order.side == Side.BUY else self.ask_levels
            levels[order.price_ticks].append(order.order_id)  # type: ignore[index]
        self.assert_valid()
        return trades

    def cancel(self, order_id: str, agent_id: str) -> Order:
        if order_id not in self.orders:
            raise KeyError(f"unknown resting order {order_id}")
        order = self.orders[order_id]
        if order.agent_id != agent_id:
            raise PermissionError("cannot cancel another agent's order")
        levels = self.bid_levels if order.side == Side.BUY else self.ask_levels
        assert order.price_ticks is not None
        queue = levels[order.price_ticks]
        queue.remove(order_id)
        if not queue:
            del levels[order.price_ticks]
        del self.orders[order_id]
        return order

    def _validate(self, order: Order) -> None:
        if order.symbol != self.symbol:
            raise ValueError(f"order symbol {order.symbol} does not match book {self.symbol}")
        if order.order_id in self.seen_ids:
            raise ValueError(f"duplicate order id {order.order_id}")
        if order.quantity % self.lot_size:
            raise ValueError(f"order quantity must be a multiple of lot size {self.lot_size}")
        if order.price_ticks is not None and order.price_ticks <= 0:
            raise ValueError("price_ticks must be positive")

    def _crosses(self, incoming: Order) -> bool:
        opposite = self.best_ask if incoming.side == Side.BUY else self.best_bid
        if opposite is None:
            return False
        if incoming.order_type == OrderType.MARKET:
            return True
        assert incoming.price_ticks is not None
        return (
            incoming.price_ticks >= opposite
            if incoming.side == Side.BUY
            else incoming.price_ticks <= opposite
        )

    def _match(self, incoming: Order, step: int) -> list[Trade]:
        trades: list[Trade] = []
        while incoming.remaining and self._crosses(incoming):
            price = self.best_ask if incoming.side == Side.BUY else self.best_bid
            assert price is not None
            levels = self.ask_levels if incoming.side == Side.BUY else self.bid_levels
            maker_id = levels[price][0]
            maker = self.orders[maker_id]
            executed = min(incoming.remaining, maker.remaining or 0)
            incoming.remaining -= executed
            maker.remaining = (maker.remaining or 0) - executed
            self.trade_sequence += 1
            buyer = incoming.agent_id if incoming.side == Side.BUY else maker.agent_id
            seller = maker.agent_id if incoming.side == Side.BUY else incoming.agent_id
            trade = Trade(
                f"{self.symbol}-T{self.trade_sequence:08d}",
                self.symbol,
                price,
                executed,
                buyer,
                seller,
                maker.order_id,
                incoming.order_id,
                step,
            )
            trades.append(trade)
            self.last_price_ticks = price
            if maker.remaining == 0:
                levels[price].popleft()
                if not levels[price]:
                    del levels[price]
                del self.orders[maker_id]
        return trades

    def snapshot(self, levels: int = 5) -> dict:
        bids = sorted(self.bid_levels, reverse=True)[:levels]
        asks = sorted(self.ask_levels)[:levels]
        return {
            "symbol": self.symbol,
            "best_bid_ticks": self.best_bid,
            "best_ask_ticks": self.best_ask,
            "last_price_ticks": self.last_price_ticks,
            "bids": [
                {
                    "price_ticks": price,
                    "quantity": sum(self.orders[oid].remaining or 0 for oid in self.bid_levels[price]),
                }
                for price in bids
            ],
            "asks": [
                {
                    "price_ticks": price,
                    "quantity": sum(self.orders[oid].remaining or 0 for oid in self.ask_levels[price]),
                }
                for price in asks
            ],
        }

    def assert_valid(self) -> None:
        if self.best_bid is not None and self.best_ask is not None and self.best_bid >= self.best_ask:
            raise AssertionError("crossed resting book")
        if any((order.remaining or 0) <= 0 for order in self.orders.values()):
            raise AssertionError("resting orders must have positive remaining quantity")
