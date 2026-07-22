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
        self._pending_log_indexes: dict[str, int] = {}

    def register(self, account: Account) -> None:
        if account.agent_id in self.accounts:
            raise ValueError(f"duplicate account {account.agent_id}")
        self.accounts[account.agent_id] = account

    def record_submission(self, order: Order) -> None:
        """Record the strategy-side submission before exchange latency elapses."""
        if order.order_id in self._pending_log_indexes:
            return
        self.order_log.append(
            {
                **order.to_dict(),
                "arrival_step": None,
                "accepted": None,
                "status": "pending_arrival",
                "rejection_reason": None,
            }
        )
        self._pending_log_indexes[order.order_id] = len(self.order_log) - 1

    def submit(self, order: Order, step: int, *, max_match_quantity: int | None = None) -> list[Trade]:
        if order.agent_id not in self.accounts:
            raise KeyError(f"unregistered agent {order.agent_id}")
        self.record_submission(order)
        log_index = self._pending_log_indexes.pop(order.order_id)
        order.exchange_arrival_step = step
        if order.exchange_arrival_time_ms is None:
            order.exchange_arrival_time_ms = step
        order.acknowledgment_time_ms = order.exchange_arrival_time_ms
        try:
            raw = self.books[order.symbol].submit(order, step, max_match_quantity=max_match_quantity)
        except Exception as exc:
            self.order_log[log_index].update(
                {
                    **order.to_dict(),
                    "arrival_step": step,
                    "accepted": False,
                    "status": "rejected",
                    "rejection_reason": str(exc),
                }
            )
            raise
        trades = [self._settle(trade) for trade in raw]
        self.trade_log.extend(trade.to_dict() for trade in trades)
        self.order_log[log_index].update(
            {
                **order.to_dict(),
                "arrival_step": step,
                "accepted": True,
                "status": "acknowledged",
                "rejection_reason": None,
            }
        )
        return trades

    def cancel(self, request: CancelRequest, symbol: str) -> None:
        order = self.books[symbol].cancel(request.order_id, request.agent_id)
        effective_step = (
            request.effective_step if request.effective_step is not None else request.submitted_step
        )
        request_time = (
            request.request_time_ms if request.request_time_ms is not None else request.submitted_step
        )
        effective_time = (
            request.effective_time_ms if request.effective_time_ms is not None else effective_step
        )
        if effective_step < request.submitted_step or effective_time < request_time:
            raise ValueError("cancel effective time cannot precede its request")
        self.cancel_log.append(
            {
                "order_id": order.order_id,
                "agent_id": request.agent_id,
                "symbol": symbol,
                "request_step": request.submitted_step,
                "effective_step": effective_step,
                "step": effective_step,
                "request_time_ms": request_time,
                "effective_time_ms": effective_time,
                "cancelled_quantity": order.remaining or 0,
            }
        )

    def cancel_pending(
        self,
        order: Order,
        *,
        request_step: int,
        request_time_ms: int,
        effective_step: int,
        effective_time_ms: int,
    ) -> None:
        """Cancel an order at the strategy gateway before it reaches the book."""
        if effective_time_ms < request_time_ms:
            raise ValueError("cancel effective time cannot precede its request")
        log_index = self._pending_log_indexes.pop(order.order_id)
        self.order_log[log_index].update(
            {
                **order.to_dict(),
                "arrival_step": None,
                "accepted": False,
                "status": "cancelled_before_arrival",
                "rejection_reason": None,
            }
        )
        self.cancel_log.append(
            {
                "order_id": order.order_id,
                "agent_id": order.agent_id,
                "symbol": order.symbol,
                "request_step": request_step,
                "effective_step": effective_step,
                "step": effective_step,
                "request_time_ms": request_time_ms,
                "effective_time_ms": effective_time_ms,
                "cancelled_quantity": order.remaining or 0,
                "pre_arrival": True,
            }
        )

    def _settle(self, trade: Trade) -> Trade:
        buyer, seller = self.accounts[trade.buyer_id], self.accounts[trade.seller_id]
        notional = trade.price_ticks * self.spec.tick_size_cents * trade.quantity
        buyer_is_maker = trade.maker_id == trade.buyer_id
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

    def finalize_order_log(self, final_step: int) -> None:
        """Resolve deterministic terminal quantities and durations for every order attempt."""
        fills: dict[str, int] = {}
        first_fill: dict[str, int] = {}
        last_fill: dict[str, int] = {}
        first_fill_time: dict[str, int | None] = {}
        last_fill_time: dict[str, int | None] = {}
        for trade in self.trade_log:
            for order_id in {trade["maker_order_id"], trade["taker_order_id"]}:
                fills[order_id] = fills.get(order_id, 0) + int(trade["quantity"])
                step = int(trade["fill_step"] if trade["fill_step"] is not None else trade["step"])
                first_fill[order_id] = min(first_fill.get(order_id, step), step)
                last_fill[order_id] = max(last_fill.get(order_id, step), step)
                fill_time = trade.get("fill_time_ms")
                if order_id not in first_fill_time:
                    first_fill_time[order_id] = fill_time
                last_fill_time[order_id] = fill_time
        cancellations = {row["order_id"]: row for row in self.cancel_log}
        live_orders = {
            order_id: order for book in self.books.values() for order_id, order in book.orders.items()
        }
        for row in self.order_log:
            order_id = row["order_id"]
            quantity = int(row["quantity"])
            filled = fills.get(order_id, 0)
            cancelled = int(cancellations.get(order_id, {}).get("cancelled_quantity", 0))
            if row["accepted"] is None:
                active = quantity
                rejected = 0
                expired = 0
                status = "pending_arrival"
            elif not row["accepted"] and cancelled:
                active = 0
                rejected = 0
                expired = 0
                status = "cancelled_before_arrival"
            elif not row["accepted"]:
                active = 0
                rejected = quantity
                expired = 0
                status = "rejected"
            else:
                active = int(live_orders[order_id].remaining or 0) if order_id in live_orders else 0
                rejected = 0
                expired = max(0, quantity - filled - cancelled - active)
                if cancelled:
                    status = "cancelled" if not filled else "partially_filled_cancelled"
                elif active:
                    status = "active" if not filled else "partially_filled_active"
                elif expired:
                    status = "expired_unfilled" if not filled else "partially_filled_expired"
                else:
                    status = "filled"
            row.update(
                {
                    "filled_quantity": filled,
                    "cancelled_quantity": cancelled,
                    "rejected_quantity": rejected,
                    "expired_quantity": expired,
                    "active_quantity": active,
                    "remaining": active,
                    "status": status,
                    "first_fill_step": first_fill.get(order_id),
                    "last_fill_step": last_fill.get(order_id),
                    "first_fill_time_ms": first_fill_time.get(order_id),
                    "last_fill_time_ms": last_fill_time.get(order_id),
                }
            )
            if int(row.get("rested_quantity_at_entry", 0)) > 0:
                endpoint = int(
                    cancellations.get(order_id, {}).get(
                        "effective_step", last_fill.get(order_id, final_step) if not active else final_step
                    )
                )
                row["resting_duration_steps"] = max(0, endpoint - int(row["arrival_step"]))
            else:
                row["resting_duration_steps"] = None

    def total_cash_cents(self) -> int:
        return sum(account.cash_cents for account in self.accounts.values()) + self.fee_account_cents

    def total_inventory(self, symbol: str) -> int:
        return sum(account.inventory.get(symbol, 0) for account in self.accounts.values())
