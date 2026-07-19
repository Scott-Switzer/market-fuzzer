"""Cash-like, price-time-priority matching over the immutable V2 event ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil

from .v2 import (
    EventKernelV2,
    EventKindV2,
    ExchangeValidationError,
    OrderCommandV2,
    OrderRejectedError,
    OrderTypeV2,
    SelfTradePreventionV2,
    SideV2,
    TimeInForceV2,
)


@dataclass(slots=True)
class AccountStateV2:
    account_id: str
    cash_cents: int
    positions: dict[str, int] = field(default_factory=dict)
    reserved_cash_cents: int = 0
    reserved_positions: dict[str, int] = field(default_factory=dict)

    def available_cash_cents(self) -> int:
        return self.cash_cents - self.reserved_cash_cents

    def available_position(self, instrument_id: str) -> int:
        return self.positions.get(instrument_id, 0) - self.reserved_positions.get(instrument_id, 0)


@dataclass(slots=True)
class RestingOrderV2:
    command: OrderCommandV2
    remaining_quantity: int


@dataclass(frozen=True, slots=True)
class TradeV2:
    trade_id: str
    instrument_id: str
    price_ticks: int
    quantity: int
    maker_order_id: str
    taker_order_id: str
    buyer_account_id: str
    seller_account_id: str
    maker_fee_cents: int
    taker_fee_cents: int


class MatchingExchangeV2:
    """Single-venue cash-like CLOB with conservative reservations and settlement.

    The engine is deliberately strict: accounts cannot spend unreserved cash or
    sell unavailable inventory.  It is a V2 component, not yet the legacy UI
    adapter; all externally visible outcomes are mirrored into ``kernel.ledger``.
    """

    def __init__(
        self,
        kernel: EventKernelV2,
        *,
        tick_size_cents: int = 1,
        maker_fee_bps: int = 0,
        taker_fee_bps: int = 0,
    ) -> None:
        if tick_size_cents <= 0 or min(maker_fee_bps, taker_fee_bps) < 0:
            raise ExchangeValidationError("tick size and fees must be non-negative with positive tick size")
        self.kernel = kernel
        self.tick_size_cents = tick_size_cents
        self.maker_fee_bps = maker_fee_bps
        self.taker_fee_bps = taker_fee_bps
        self.accounts: dict[str, AccountStateV2] = {}
        self._books: dict[str, dict[SideV2, dict[int, list[str]]]] = {}
        self._orders: dict[str, RestingOrderV2] = {}
        self._sequence = 0
        self._trade_sequence = 0
        self.fee_account_cents = 0

    def register(self, account: AccountStateV2) -> None:
        if account.account_id in self.accounts:
            raise ExchangeValidationError(f"duplicate account {account.account_id}")
        if account.cash_cents < 0 or any(quantity < 0 for quantity in account.positions.values()):
            raise ExchangeValidationError("cash and initial positions must be non-negative")
        self.accounts[account.account_id] = account

    def submit(self, command: OrderCommandV2) -> tuple[TradeV2, ...]:
        acknowledgement = self.kernel.admit(command)
        if acknowledgement.kind == EventKindV2.ORDER_REJECTED:
            return ()
        if command.account_id not in self.accounts:
            self._reject(command, "unknown_account")
            return ()
        try:
            self._reserve(command)
        except OrderRejectedError as error:
            self._reject(command, str(error))
            return ()
        if command.time_in_force == TimeInForceV2.FOK and not self._can_fully_execute(command):
            self._release(command, command.quantity)
            self._reject(command, "fok_not_fillable")
            return ()
        incoming = RestingOrderV2(command, command.quantity)
        trades = self._match(incoming)
        if (
            incoming.remaining_quantity
            and command.time_in_force == TimeInForceV2.DAY
            and command.order_type == OrderTypeV2.LIMIT
        ):
            self._rest(incoming)
        elif incoming.remaining_quantity:
            self._release(command, incoming.remaining_quantity)
            self._event(
                command,
                EventKindV2.ORDER_CANCELLED,
                {"reason": "ioc_or_market_remainder", "quantity": incoming.remaining_quantity},
            )
        self._assert_conservation()
        return tuple(trades)

    def cancel(self, *, account_id: str, order_id: str, exchange_time_ns: int, venue_sequence: int) -> None:
        order = self._orders.get(order_id)
        if order is None:
            raise OrderRejectedError("unknown_resting_order")
        if order.command.account_id != account_id:
            raise OrderRejectedError("cancel_not_owner")
        level = self._levels(order.command.instrument_id, order.command.side)[order.command.price_ticks or 0]
        level.remove(order_id)
        if not level:
            del self._levels(order.command.instrument_id, order.command.side)[order.command.price_ticks or 0]
        del self._orders[order_id]
        self._release(order.command, order.remaining_quantity)
        self._event(
            order.command,
            EventKindV2.ORDER_CANCELLED,
            {"quantity": order.remaining_quantity},
            exchange_time_ns,
            venue_sequence,
        )
        self._assert_conservation()

    def _reserve(self, command: OrderCommandV2) -> None:
        account = self.accounts[command.account_id]
        if command.side == SideV2.SELL:
            if account.available_position(command.instrument_id) < command.quantity:
                raise OrderRejectedError("insufficient_available_position")
            account.reserved_positions[command.instrument_id] = (
                account.reserved_positions.get(command.instrument_id, 0) + command.quantity
            )
            return
        if command.order_type != OrderTypeV2.LIMIT or command.price_ticks is None:
            raise OrderRejectedError("market_buy_requires_price_protection")
        needed = self._maximum_cost(command.price_ticks, command.quantity)
        if account.available_cash_cents() < needed:
            raise OrderRejectedError("insufficient_available_cash")
        account.reserved_cash_cents += needed

    def _release(self, command: OrderCommandV2, quantity: int) -> None:
        account = self.accounts[command.account_id]
        if command.side == SideV2.SELL:
            account.reserved_positions[command.instrument_id] -= quantity
            if account.reserved_positions[command.instrument_id] == 0:
                del account.reserved_positions[command.instrument_id]
        else:
            assert command.price_ticks is not None
            account.reserved_cash_cents -= self._maximum_cost(command.price_ticks, quantity)

    def _can_fully_execute(self, command: OrderCommandV2) -> bool:
        executable = 0
        for price in self._opposite_prices(command):
            if not self._crosses(command, price):
                break
            for order_id in self._levels(command.instrument_id, command.side.opposite)[price]:
                maker = self._orders[order_id]
                if maker.command.account_id == command.account_id:
                    return False
                executable += maker.remaining_quantity
                if executable >= command.quantity:
                    return True
        return False

    def _match(self, incoming: RestingOrderV2) -> list[TradeV2]:
        trades: list[TradeV2] = []
        while incoming.remaining_quantity:
            prices = self._opposite_prices(incoming.command)
            if not prices or not self._crosses(incoming.command, prices[0]):
                break
            price = prices[0]
            queue = self._levels(incoming.command.instrument_id, incoming.command.side.opposite)[price]
            maker = self._orders[queue[0]]
            if maker.command.account_id == incoming.command.account_id:
                self._apply_self_trade_prevention(incoming, maker)
                if incoming.remaining_quantity == 0:
                    break
                continue
            quantity = min(incoming.remaining_quantity, maker.remaining_quantity)
            trade = self._settle(maker, incoming, price, quantity)
            trades.append(trade)
            incoming.remaining_quantity -= quantity
            maker.remaining_quantity -= quantity
            self._release(incoming.command, quantity)
            self._release(maker.command, quantity)
            self._event(
                incoming.command,
                EventKindV2.TRADE_EXECUTED,
                {"trade_id": trade.trade_id, "quantity": quantity, "price_ticks": price},
            )
            if maker.remaining_quantity == 0:
                queue.pop(0)
                del self._orders[maker.command.order_id]
                if not queue:
                    del self._levels(maker.command.instrument_id, maker.command.side)[price]
        return trades

    def _apply_self_trade_prevention(self, incoming: RestingOrderV2, maker: RestingOrderV2) -> None:
        policy = incoming.command.self_trade_prevention
        if policy == SelfTradePreventionV2.CANCEL_TAKER:
            self._release(incoming.command, incoming.remaining_quantity)
            incoming.remaining_quantity = 0
            self._event(incoming.command, EventKindV2.ORDER_CANCELLED, {"reason": "self_trade_prevention"})
            return
        if policy == SelfTradePreventionV2.CANCEL_MAKER:
            level = self._levels(maker.command.instrument_id, maker.command.side)[
                maker.command.price_ticks or 0
            ]
            level.remove(maker.command.order_id)
            self._release(maker.command, maker.remaining_quantity)
            del self._orders[maker.command.order_id]
            if not level:
                del self._levels(maker.command.instrument_id, maker.command.side)[
                    maker.command.price_ticks or 0
                ]
            self._event(
                maker.command,
                EventKindV2.ORDER_CANCELLED,
                {"reason": "self_trade_prevention"},
                incoming.command.exchange_time_ns,
                incoming.command.venue_sequence,
            )
            return
        quantity = min(incoming.remaining_quantity, maker.remaining_quantity)
        incoming.remaining_quantity -= quantity
        maker.remaining_quantity -= quantity
        self._release(incoming.command, quantity)
        self._release(maker.command, quantity)
        if maker.remaining_quantity == 0:
            level = self._levels(maker.command.instrument_id, maker.command.side)[
                maker.command.price_ticks or 0
            ]
            level.remove(maker.command.order_id)
            del self._orders[maker.command.order_id]
            if not level:
                del self._levels(maker.command.instrument_id, maker.command.side)[
                    maker.command.price_ticks or 0
                ]

    def _settle(self, maker: RestingOrderV2, taker: RestingOrderV2, price: int, quantity: int) -> TradeV2:
        buyer = taker if taker.command.side == SideV2.BUY else maker
        seller = maker if taker.command.side == SideV2.BUY else taker
        notional = price * self.tick_size_cents * quantity
        maker_fee = self._fee(notional, self.maker_fee_bps)
        taker_fee = self._fee(notional, self.taker_fee_bps)
        buyer_account, seller_account = (
            self.accounts[buyer.command.account_id],
            self.accounts[seller.command.account_id],
        )
        buyer_fee = maker_fee if buyer is maker else taker_fee
        seller_fee = maker_fee if seller is maker else taker_fee
        buyer_account.cash_cents -= notional + buyer_fee
        seller_account.cash_cents += notional - seller_fee
        buyer_account.positions[buyer.command.instrument_id] = (
            buyer_account.positions.get(buyer.command.instrument_id, 0) + quantity
        )
        seller_account.positions[seller.command.instrument_id] = (
            seller_account.positions.get(seller.command.instrument_id, 0) - quantity
        )
        self.fee_account_cents += maker_fee + taker_fee
        self._trade_sequence += 1
        return TradeV2(
            f"trade-{self._trade_sequence:020d}",
            buyer.command.instrument_id,
            price,
            quantity,
            maker.command.order_id,
            taker.command.order_id,
            buyer.command.account_id,
            seller.command.account_id,
            maker_fee,
            taker_fee,
        )

    def _rest(self, order: RestingOrderV2) -> None:
        assert order.command.price_ticks is not None
        self._levels(order.command.instrument_id, order.command.side).setdefault(
            order.command.price_ticks, []
        ).append(order.command.order_id)
        self._orders[order.command.order_id] = order

    def _levels(self, instrument_id: str, side: SideV2) -> dict[int, list[str]]:
        book = self._books.setdefault(instrument_id, {SideV2.BUY: {}, SideV2.SELL: {}})
        return book[side]

    def _opposite_prices(self, command: OrderCommandV2) -> list[int]:
        prices = self._levels(command.instrument_id, command.side.opposite)
        return sorted(prices, reverse=command.side == SideV2.SELL)

    @staticmethod
    def _crosses(command: OrderCommandV2, price: int) -> bool:
        if command.order_type == OrderTypeV2.MARKET:
            return True
        assert command.price_ticks is not None
        return command.price_ticks >= price if command.side == SideV2.BUY else command.price_ticks <= price

    def _maximum_cost(self, price_ticks: int, quantity: int) -> int:
        notional = price_ticks * self.tick_size_cents * quantity
        return notional + self._fee(notional, max(self.maker_fee_bps, self.taker_fee_bps))

    @staticmethod
    def _fee(notional_cents: int, fee_bps: int) -> int:
        return ceil(notional_cents * fee_bps / 10_000)

    def _event(
        self,
        command: OrderCommandV2,
        kind: EventKindV2,
        payload: dict[str, object],
        exchange_time_ns: int | None = None,
        venue_sequence: int | None = None,
    ) -> None:
        self._sequence += 1
        self.kernel.ledger.append(
            self.kernel.ledger.events[0].__class__(
                event_id=f"match-{self._sequence:020d}",
                kind=kind,
                exchange_time_ns=command.exchange_time_ns if exchange_time_ns is None else exchange_time_ns,
                venue_sequence=command.venue_sequence if venue_sequence is None else venue_sequence,
                event_priority=20,
                command_id=command.command_id,
                order_id=command.order_id,
                payload=dict(payload),
            )
        )

    def _reject(self, command: OrderCommandV2, reason: str) -> None:
        self._event(command, EventKindV2.ORDER_REJECTED, {"reason": reason})

    def _assert_conservation(self) -> None:
        if any(account.cash_cents < account.reserved_cash_cents for account in self.accounts.values()):
            raise AssertionError("reservation exceeds cash")
        if any(
            account.available_position(symbol) < 0
            for account in self.accounts.values()
            for symbol in account.positions
        ):
            raise AssertionError("reservation exceeds position")
