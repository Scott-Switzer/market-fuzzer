from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from app.break_test.strategies import compute_positions
from app.exchange import (
    EventKernelV2,
    OrderCommandV2,
    OrderTypeV2,
    RunManifestV2,
    SideV2,
    TimeInForceV2,
)
from app.exchange.market import Account
from app.exchange.order_book import OrderBook
from app.exchange.orders import CancelRequest, Order, OrderType, Side, Trade


@dataclass(frozen=True)
class BridgeTrade:
    step: int
    price_ticks: int
    quantity: int
    buyer_id: str
    seller_id: str
    maker_order_id: str
    taker_order_id: str
    maker_fee_cents: int
    taker_fee_cents: int


@dataclass(frozen=True)
class BridgeOrderLog:
    order_id: str
    agent_id: str
    step: int
    quantity: int
    price_ticks: int | None
    accepted: bool
    status: str
    filled_quantity: int
    rejected_quantity: int
    cancelled_quantity: int
    active_quantity: int
    remaining: int


@dataclass(frozen=True)
class UserStrategyResult:
    manifest_digest: str
    ledger_digest: str
    seed: int
    price_path: list[float]
    mid_series: list[float]
    cash_series: list[int]
    inventory: list[int]
    submitted: list[BridgeOrderLog]
    trades: list[BridgeTrade]
    fees_cents: int
    pnl_cents: int
    order_sequence: tuple[str, ...]


class DeterministicUserStrategyBridge:
    USER_ACCOUNT_ID = "user-strategy-account"
    MM_ACCOUNT_ID = "mm-agent"
    MM_CAPITAL_CENTS = 500_000_000
    USER_INITIAL_CASH_CENTS = 1_000_000_000
    USER_INITIAL_INVENTORY = 0
    TICK_SIZE_CENTS = 1
    USER_FEE_BPS = 0.3
    MM_FEE_BPS = -0.1
    MM_LEVELS = 3

    def __init__(self, symbol: str = "SYNTH", lot_size: int = 1) -> None:
        self.symbol = symbol
        self.lot_size = lot_size

    def _stable_hash(self, value: object) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

    def _make_manifest(
        self, world_id: str, seed: int, strategy_type: str, params: dict[str, int]
    ) -> RunManifestV2:
        spec_digest = self._stable_hash({"world_id": world_id, "seed": seed, "lot_size": self.lot_size})
        strategy_digest = self._stable_hash({"type": strategy_type, "params": params})
        return RunManifestV2(
            specification_digest=spec_digest,
            strategy_artifact_digest=strategy_digest,
            generator_bundle_digest=self._stable_hash({"generator": "exchange_fwd_bridge"}),
            campaign_commitment=self._stable_hash({"seed": seed, "world_id": world_id}),
            seed_material_digest=self._stable_hash(
                {"seed": seed, "strategy": strategy_type, "params": params}
            ),
            protocol_version="event-kernel-v2",
        )

    def _make_kernel(
        self, world_id: str, seed: int, strategy_type: str, params: dict[str, int]
    ) -> tuple[EventKernelV2, RunManifestV2]:
        manifest = self._make_manifest(world_id, seed, strategy_type, params)
        return EventKernelV2(manifest), manifest

    class _Exchange:
        def __init__(self, symbol: str, lot_size: int) -> None:
            self.symbol = symbol
            self.lot_size = lot_size
            self.books: dict[str, OrderBook] = {symbol: OrderBook(symbol, 1, lot_size)}
            self.accounts: dict[str, Account] = {}
            self.order_log: list[dict] = []
            self.trade_log: list[Trade] = []
            self.cancel_log: list[dict] = []
            self.fee_account_cents = 0
            self._pending_log_indexes: dict[str, int] = {}
            self.tick_size_cents = 1
            self.maker_fee_bps = DeterministicUserStrategyBridge.MM_FEE_BPS
            self.taker_fee_bps = DeterministicUserStrategyBridge.USER_FEE_BPS
            self.mm_account_id = DeterministicUserStrategyBridge.MM_ACCOUNT_ID

        def register(self, account: Account) -> None:
            if account.agent_id in self.accounts:
                raise ValueError(f"duplicate account {account.agent_id}")
            self.accounts[account.agent_id] = account

        def record_submission(self, order: Order) -> None:
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

        def _assure_mm_liquidity(self, side: Side, step: int) -> None:
            book = self.books[self.symbol]
            if side == Side.BUY:
                best_ask = book.best_ask
                if best_ask is None or best_ask <= 0:
                    return
                existing_ask_qty = sum(
                    order.quantity
                    for order in book.orders.values()
                    if order.side == Side.SELL and order.price_ticks == best_ask
                )
                if existing_ask_qty < self.lot_size * 4:
                    ask_order = Order(
                        order_id=f"{self.mm_account_id}-relist-ask-{step:06d}",
                        agent_id=self.mm_account_id,
                        symbol=self.symbol,
                        side=Side.SELL,
                        order_type=OrderType.LIMIT,
                        quantity=self.lot_size * 20,
                        submitted_step=step,
                        price_ticks=best_ask + 1,
                    )
                    self.submit(ask_order, step)
            else:
                best_bid = book.best_bid
                if best_bid is None or best_bid <= 0:
                    return
                existing_bid_qty = sum(
                    order.quantity
                    for order in book.orders.values()
                    if order.side == Side.BUY and order.price_ticks == best_bid
                )
                if existing_bid_qty < self.lot_size * 4:
                    bid_order = Order(
                        order_id=f"{self.mm_account_id}-relist-bid-{step:06d}",
                        agent_id=self.mm_account_id,
                        symbol=self.symbol,
                        side=Side.BUY,
                        order_type=OrderType.LIMIT,
                        quantity=self.lot_size * 20,
                        submitted_step=step,
                        price_ticks=best_bid - 1,
                    )
                    self.submit(bid_order, step)

        def submit(self, order: Order, step: int) -> list[Trade]:
            if order.agent_id not in self.accounts:
                raise KeyError(f"unregistered agent {order.agent_id}")
            if order.agent_id == self.mm_account_id:
                self.record_submission(order)
                log_index = self._pending_log_indexes.pop(order.order_id)
                raw = self.books[order.symbol].submit(order, step)
                trades = [self._settle(trade) for trade in raw]
                self.trade_log.extend(trades)
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
            self._assure_mm_liquidity(order.side, step)
            self.record_submission(order)
            log_index = self._pending_log_indexes.pop(order.order_id)
            order.exchange_arrival_step = step
            if order.exchange_arrival_time_ms is None:
                order.exchange_arrival_time_ms = step
            order.acknowledgment_time_ms = order.exchange_arrival_time_ms
            try:
                raw = self.books[order.symbol].submit(order, step)
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
            self.trade_log.extend(trades)
            self.order_log[log_index].update(
                {
                    **order.to_dict(),
                    "arrival_step": step,
                    "accepted": True,
                    "status": "acknowledged",
                    "rejection_reason": None,
                }
            )
            self._assure_mm_liquidity(order.side, step)
            return trades

        def _settle(self, trade: Trade) -> Trade:
            buyer = self.accounts[trade.buyer_id]
            seller = self.accounts[trade.seller_id]
            notional = trade.price_ticks * self.tick_size_cents * trade.quantity
            buyer_fee = round(notional * self.taker_fee_bps / 10_000)
            seller_fee = round(notional * self.maker_fee_bps / 10_000)
            buyer.cash_cents -= notional + buyer_fee
            seller.cash_cents += notional - seller_fee
            buyer.inventory[trade.symbol] = buyer.inventory.get(trade.symbol, 0) + trade.quantity
            seller.inventory[trade.symbol] = seller.inventory.get(trade.symbol, 0) - trade.quantity
            self.fee_account_cents += buyer_fee + seller_fee
            return Trade(
                trade.trade_id,
                trade.symbol,
                trade.price_ticks,
                trade.quantity,
                trade.buyer_id,
                trade.seller_id,
                trade.maker_order_id,
                trade.taker_order_id,
                trade.step,
                trade.maker_id,
                trade.taker_id,
                trade.arrival_step,
                trade.fill_step,
                trade.arrival_time_ms,
                trade.fill_time_ms,
                trade.fill_sequence,
                trade.maker_partial_fill_sequence,
                trade.taker_partial_fill_sequence,
                trade.maker_queue_ahead_at_entry,
                trade.quantity_traded_at_level_before_fill,
                seller_fee,
                buyer_fee,
            )

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
                    "cancelled_quantity": max(0, int(order.remaining or 0)),
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
                    "cancelled_quantity": max(0, int(order.remaining or 0)),
                    "pre_arrival": True,
                }
            )

        def finalize_order_log(self, final_step: int) -> None:
            fills: dict[str, int] = {}
            for trade in self.trade_log:
                for order_id in {trade.maker_order_id, trade.taker_order_id}:
                    fills[order_id] = fills.get(order_id, 0) + int(trade.quantity)
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
                    }
                )

    def _seed_book_with_market_makers(self, exchange: _Exchange, initial_mid: int) -> None:
        exchange.register(Account(self.MM_ACCOUNT_ID, self.MM_CAPITAL_CENTS, {self.symbol: 0}))
        exchange.books[self.symbol]
        for level in range(1, self.MM_LEVELS + 1):
            bid_price = max(1, initial_mid - level * 3)
            ask_price = initial_mid + level * 3
            quantity = max(self.lot_size, 120 // level * self.lot_size)
            bid_order = Order(
                order_id=f"mm-seed-bid-{level:02d}",
                agent_id=self.MM_ACCOUNT_ID,
                symbol=self.symbol,
                side=Side.BUY,
                order_type=OrderType.LIMIT,
                quantity=quantity,
                submitted_step=0,
                price_ticks=bid_price,
            )
            ask_order = Order(
                order_id=f"mm-seed-ask-{level:02d}",
                agent_id=self.MM_ACCOUNT_ID,
                symbol=self.symbol,
                side=Side.SELL,
                order_type=OrderType.LIMIT,
                quantity=quantity,
                submitted_step=0,
                price_ticks=ask_price,
            )
            exchange.submit(bid_order, 0)
            exchange.submit(ask_order, 0)

    def _snapshot_mid(self, exchange: _Exchange) -> int:
        book = exchange.books[self.symbol]
        snapshot = book.snapshot()
        best_bid = snapshot["best_bid_ticks"]
        best_ask = snapshot["best_ask_ticks"]
        if best_bid is not None and best_ask is not None:
            return round((best_bid + best_ask) / 2)
        last = book.last_price_ticks
        if last is not None:
            return last
        raise RuntimeError("no mid price available")

    def run_user_strategy(
        self,
        world_id: str,
        seed: int,
        prices: Sequence[float],
        strategy_type: str,
        params: dict[str, int],
    ) -> UserStrategyResult:
        kernel, manifest = self._make_kernel(world_id, seed, strategy_type, params)
        local_accounts: dict[str, Account] = {
            self.USER_ACCOUNT_ID: Account(
                self.USER_ACCOUNT_ID, self.USER_INITIAL_CASH_CENTS, {self.symbol: self.USER_INITIAL_INVENTORY}
            ),
            self.MM_ACCOUNT_ID: Account(self.MM_ACCOUNT_ID, self.MM_CAPITAL_CENTS, {self.symbol: 0}),
        }
        exchange = self._Exchange(self.symbol, self.lot_size, local_accounts)
        local_accounts[self.USER_ACCOUNT_ID]
        self._seed_book_with_market_makers(exchange, initial_mid=int(round(float(prices[0]))))
        current_inventory = 0
        current_cash = self.USER_INITIAL_CASH_CENTS
        order_logs: list[BridgeOrderLog] = []
        trade_logs: list[BridgeTrade] = []
        inventory_history: list[int] = [current_inventory]
        cash_history: list[int] = [current_cash]
        mid_history: list[float] = [float(self._snapshot_mid(exchange))]
        order_sequence: list[str] = []
        for step, _price in enumerate(prices):
            mid = self._snapshot_mid(exchange)
            prices_seq = [float(p) for p in prices[: step + 1]]
            orders = self._translate_strategy_to_orders(
                strategy_type=strategy_type,
                prices=np.asarray(prices_seq, dtype=float),
                params=params,
                current_inventory=current_inventory,
                exchange=exchange,
            )
            for order in orders:
                kernel.admit(
                    OrderCommandV2(
                        command_id=f"cmd-{step:06d}-{order.order_id}",
                        order_id=order.order_id,
                        account_id=order.agent_id,
                        instrument_id=order.symbol,
                        side=SideV2.BUY if order.side == Side.BUY else SideV2.SELL,
                        order_type=OrderTypeV2.MARKET
                        if order.order_type == OrderType.MARKET
                        else OrderTypeV2.LIMIT,
                        quantity=order.quantity,
                        exchange_time_ns=step * 1_000_000_000,
                        venue_sequence=step,
                        price_ticks=order.price_ticks,
                        time_in_force=TimeInForceV2.DAY,
                    )
                )
                trades = exchange.submit(order, step)
                for trade in trades:
                    buy_fee_cents = round(trade.price_ticks * trade.quantity * 0.01)
                    sell_fee_cents = round(trade.price_ticks * trade.quantity * -0.001)
                    if trade.buyer_id == self.USER_ACCOUNT_ID:
                        current_cash -= trade.price_ticks * trade.quantity + buy_fee_cents
                        current_inventory += trade.quantity
                    if trade.seller_id == self.USER_ACCOUNT_ID:
                        current_cash += trade.price_ticks * trade.quantity - sell_fee_cents
                    trade_logs.append(
                        BridgeTrade(
                            step=step,
                            price_ticks=trade.price_ticks,
                            quantity=trade.quantity,
                            buyer_id=trade.buyer_id,
                            seller_id=trade.seller_id,
                            maker_order_id=trade.maker_order_id,
                            taker_order_id=trade.taker_order_id,
                            maker_fee_cents=sell_fee_cents
                            if trade.maker_id == trade.seller_id
                            else buy_fee_cents,
                            taker_fee_cents=buy_fee_cents
                            if trade.taker_id == trade.buyer_id
                            else sell_fee_cents,
                        )
                    )
                filled = sum(t.quantity for t in trades)
                order_sequence.append(order.order_id)
                order_logs.append(
                    BridgeOrderLog(
                        order_id=order.order_id,
                        agent_id=order.agent_id,
                        step=step,
                        quantity=order.quantity,
                        price_ticks=order.price_ticks,
                        accepted=True,
                        status="acknowledged",
                        filled_quantity=filled,
                        rejected_quantity=0,
                        cancelled_quantity=max(0, order.quantity - filled),
                        active_quantity=order.quantity - filled,
                        remaining=order.quantity - filled,
                    )
                )
            inventory_history.append(current_inventory)
            cash_history.append(current_cash)
            mid_history.append(float(mid))

        return UserStrategyResult(
            manifest_digest=manifest.canonical_bytes().decode(),
            ledger_digest=kernel.ledger.digest,
            seed=seed,
            price_path=[float(p) for p in prices],
            mid_series=mid_history,
            cash_series=cash_history,
            inventory=inventory_history,
            submitted=order_logs,
            trades=trade_logs,
            fees_cents=exchange.fee_account_cents,
            pnl_cents=current_cash - self.USER_INITIAL_CASH_CENTS,
            order_sequence=tuple(order_sequence),
        )

    def _translate_strategy_to_orders(
        self,
        strategy_type: str,
        prices: np.ndarray,
        params: dict[str, int],
        current_inventory: int,
        exchange: _Exchange,
    ) -> list[Order]:
        if prices.size <= max(
            2, params.get("slow", 50), params.get("entry_lookback", 20), params.get("period", 14)
        ):
            return []
        try:
            positions = compute_positions(strategy_type, prices, **params)
        except ValueError:
            return []
        if len(positions) == 0:
            return []
        desired = float(positions[-1])
        current = 1.0 if current_inventory > 0 else 0.0 if current_inventory < 0 else 0.0
        if desired <= current:
            return []
        quantity = max(self.lot_size, 100)
        quantity = (quantity // self.lot_size) * self.lot_size
        order_id = f"{self.USER_ACCOUNT_ID}-O{len(exchange.order_log) + 1:07d}"
        return [
            Order(
                order_id=order_id,
                agent_id=self.USER_ACCOUNT_ID,
                symbol=self.symbol,
                side=Side.BUY,
                order_type=OrderType.LIMIT,
                quantity=quantity,
                submitted_step=self._snapshot_mid(exchange),
                price_ticks=self._snapshot_mid(exchange),
            )
        ]


__all__ = [
    "DeterministicUserStrategyBridge",
    "BridgeTrade",
]
