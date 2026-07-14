from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

from app.schemas import ExchangeSpec

from .order_book import OrderBook
from .orders import CancelRequest, Order, Trade


@dataclass
class Account:
    agent_id: str
    cash_cents: int
    inventory: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class Exchange:
    def __init__(self, symbols: list[str], spec: ExchangeSpec) -> None:
        self.spec = spec
        self.books = {symbol: OrderBook(symbol, spec.tick_size_cents, spec.lot_size) for symbol in symbols}
        self.accounts: dict[str, Account] = {}
        self.order_log: list[dict] = []
        self.trade_log: list[dict] = []
        self.cancel_log: list[dict] = []
        self.fee_account_cents = 0

    def register(self, account: Account) -> None:
        if account.agent_id in self.accounts:
            raise ValueError(f"duplicate account {account.agent_id}")
        self.accounts[account.agent_id] = account

    def submit(self, order: Order, step: int) -> list[Trade]:
        if order.agent_id not in self.accounts:
            raise KeyError(f"unregistered agent {order.agent_id}")
        self.order_log.append({**order.to_dict(), "arrival_step": step})
        raw = self.books[order.symbol].submit(order, step)
        trades = [self._settle(trade) for trade in raw]
        self.trade_log.extend(trade.to_dict() for trade in trades)
        return trades

    def cancel(self, request: CancelRequest, symbol: str) -> None:
        order = self.books[symbol].cancel(request.order_id, request.agent_id)
        self.cancel_log.append(
            {"order_id": order.order_id, "agent_id": request.agent_id, "step": request.submitted_step}
        )

    def _settle(self, trade: Trade) -> Trade:
        buyer, seller = self.accounts[trade.buyer_id], self.accounts[trade.seller_id]
        notional = trade.price_ticks * self.spec.tick_size_cents * trade.quantity
        maker_id = self.books[trade.symbol].seen_ids and trade.maker_order_id
        maker_agent = next(
            (row["agent_id"] for row in reversed(self.order_log) if row["order_id"] == maker_id),
            trade.seller_id,
        )
        buyer_is_maker = maker_agent == trade.buyer_id
        buyer_bps = self.spec.maker_fee_bps if buyer_is_maker else self.spec.taker_fee_bps
        seller_bps = self.spec.taker_fee_bps if buyer_is_maker else self.spec.maker_fee_bps
        buyer_fee = round(notional * buyer_bps / 10_000)
        seller_fee = round(notional * seller_bps / 10_000)
        buyer.cash_cents -= notional + buyer_fee
        seller.cash_cents += notional - seller_fee
        buyer.inventory[trade.symbol] = buyer.inventory.get(trade.symbol, 0) + trade.quantity
        seller.inventory[trade.symbol] = seller.inventory.get(trade.symbol, 0) - trade.quantity
        self.fee_account_cents += buyer_fee + seller_fee
        return replace(
            trade,
            maker_fee_cents=round(notional * self.spec.maker_fee_bps / 10_000),
            taker_fee_cents=round(notional * self.spec.taker_fee_bps / 10_000),
        )

    def total_cash_cents(self) -> int:
        return sum(account.cash_cents for account in self.accounts.values()) + self.fee_account_cents

    def total_inventory(self, symbol: str) -> int:
        return sum(account.inventory.get(symbol, 0) for account in self.accounts.values())
