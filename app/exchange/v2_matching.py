"""Cash-like, price-time-priority matching over the immutable V2 event ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from enum import StrEnum
from math import ceil

from .v2 import (
    CancelOrderCommandV2,
    EventKernelV2,
    EventKindV2,
    ExchangeInvariantError,
    ExchangeValidationError,
    OrderCommandV2,
    OrderEventV2,
    OrderRejectedError,
    OrderTypeV2,
    ReplaceOrderCommandV2,
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


@dataclass(frozen=True, slots=True)
class AccountRiskLimitsV2:
    """Fail-closed admission limits for the cash-like venue profile."""

    max_order_quantity: int | None = None
    max_order_notional_cents: int | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.max_order_quantity, "max_order_quantity"),
            (self.max_order_notional_cents, "max_order_notional_cents"),
        ):
            if value is not None and value <= 0:
                raise ExchangeValidationError(f"{name} must be positive when configured")


class SessionStateV2(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


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


@dataclass(frozen=True, slots=True)
class OpenOrderSnapshotV2:
    """Read-only resting-order view suitable for the owning strategy's observation."""

    order_id: str
    instrument_id: str
    side: SideV2
    remaining_quantity: int
    limit_price_ticks: int


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
        self._risk_limits: dict[str, AccountRiskLimitsV2] = {}
        self._killed_accounts: set[str] = set()
        self._halted_instruments: set[str] = set()
        self.session_state = SessionStateV2.OPEN
        self._books: dict[str, dict[SideV2, dict[int, list[str]]]] = {}
        self._orders: dict[str, RestingOrderV2] = {}
        self._sequence = 0
        self._trade_sequence = 0
        self.fee_account_cents = 0

    def register(self, account: AccountStateV2, *, risk_limits: AccountRiskLimitsV2 | None = None) -> None:
        if account.account_id in self.accounts:
            raise ExchangeValidationError(f"duplicate account {account.account_id}")
        if account.cash_cents < 0 or any(quantity < 0 for quantity in account.positions.values()):
            raise ExchangeValidationError("cash and initial positions must be non-negative")
        self.accounts[account.account_id] = account
        self._risk_limits[account.account_id] = risk_limits or AccountRiskLimitsV2()

    def best_quote(self, instrument_id: str) -> tuple[int | None, int | None]:
        """Current displayed best bid/offer; never exposes queue identity or hidden provenance."""
        bids = self._levels(instrument_id, SideV2.BUY)
        asks = self._levels(instrument_id, SideV2.SELL)
        return (max(bids) if bids else None, min(asks) if asks else None)

    def open_orders_for(self, account_id: str, instrument_id: str) -> tuple[OpenOrderSnapshotV2, ...]:
        """Return only the caller's own resting orders, without queue position or other account data."""
        return tuple(
            OpenOrderSnapshotV2(
                order.command.order_id,
                order.command.instrument_id,
                order.command.side,
                order.remaining_quantity,
                order.command.price_ticks or 0,
            )
            for _, order in sorted(self._orders.items())
            if order.command.account_id == account_id and order.command.instrument_id == instrument_id
        )

    def close_session(self, *, exchange_time_ns: int, venue_sequence: int) -> None:
        if self.session_state == SessionStateV2.CLOSED:
            raise ExchangeValidationError("session is already closed")
        self.session_state = SessionStateV2.CLOSED
        self._control_event(EventKindV2.SESSION_CLOSED, "session", exchange_time_ns, venue_sequence)
        for order_id in sorted(self._orders):
            order = self._orders.get(order_id)
            if order is not None and order.command.time_in_force == TimeInForceV2.DAY:
                self.cancel(
                    account_id=order.command.account_id,
                    order_id=order_id,
                    exchange_time_ns=exchange_time_ns,
                    venue_sequence=venue_sequence,
                )

    def open_session(self, *, exchange_time_ns: int, venue_sequence: int) -> None:
        if self.session_state == SessionStateV2.OPEN:
            raise ExchangeValidationError("session is already open")
        self.session_state = SessionStateV2.OPEN
        self._control_event(EventKindV2.SESSION_OPENED, "session", exchange_time_ns, venue_sequence)

    def halt_instrument(self, instrument_id: str, *, exchange_time_ns: int, venue_sequence: int) -> None:
        if not instrument_id or instrument_id in self._halted_instruments:
            raise ExchangeValidationError("instrument must be active before it can halt")
        self._halted_instruments.add(instrument_id)
        self._control_event(EventKindV2.INSTRUMENT_HALTED, instrument_id, exchange_time_ns, venue_sequence)

    def resume_instrument(self, instrument_id: str, *, exchange_time_ns: int, venue_sequence: int) -> None:
        if instrument_id not in self._halted_instruments:
            raise ExchangeValidationError("instrument is not halted")
        self._halted_instruments.remove(instrument_id)
        self._control_event(EventKindV2.INSTRUMENT_RESUMED, instrument_id, exchange_time_ns, venue_sequence)

    def set_kill_switch(
        self,
        account_id: str,
        *,
        enabled: bool,
        exchange_time_ns: int,
        venue_sequence: int,
    ) -> None:
        if account_id not in self.accounts:
            raise ExchangeValidationError("unknown account")
        if enabled == (account_id in self._killed_accounts):
            raise ExchangeValidationError("kill switch already has requested state")
        if enabled:
            self._killed_accounts.add(account_id)
            self._control_event(EventKindV2.KILL_SWITCH_ENABLED, account_id, exchange_time_ns, venue_sequence)
            for order_id in sorted(
                order_id for order_id, order in self._orders.items() if order.command.account_id == account_id
            ):
                self.cancel(
                    account_id=account_id,
                    order_id=order_id,
                    exchange_time_ns=exchange_time_ns,
                    venue_sequence=venue_sequence,
                )
        else:
            self._killed_accounts.remove(account_id)
            self._control_event(
                EventKindV2.KILL_SWITCH_DISABLED, account_id, exchange_time_ns, venue_sequence
            )

    def submit(self, command: OrderCommandV2) -> tuple[TradeV2, ...]:
        acknowledgement = self.kernel.admit(command)
        if acknowledgement.kind == EventKindV2.ORDER_REJECTED:
            return ()
        if self.session_state != SessionStateV2.OPEN:
            self._reject(command, "session_closed")
            return ()
        if command.instrument_id in self._halted_instruments:
            self._reject(command, "instrument_halted")
            return ()
        if command.account_id in self._killed_accounts:
            self._reject(command, "kill_switch_enabled")
            return ()
        if command.account_id not in self.accounts:
            self._reject(command, "unknown_account")
            return ()
        try:
            self._validate_risk(command)
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

    def cancel_command(self, command: CancelOrderCommandV2) -> bool:
        """Process a uniquely identified cancel with auditable acceptance or rejection."""
        self.kernel.admit_cancel(command)
        order = self._orders.get(command.order_id)
        if order is None:
            self._command_event(command, EventKindV2.CANCEL_REJECTED, {"reason": "unknown_resting_order"})
            return False
        if order.command.account_id != command.account_id:
            self._command_event(command, EventKindV2.CANCEL_REJECTED, {"reason": "cancel_not_owner"})
            return False
        level = self._levels(order.command.instrument_id, order.command.side)[order.command.price_ticks or 0]
        level.remove(command.order_id)
        if not level:
            del self._levels(order.command.instrument_id, order.command.side)[order.command.price_ticks or 0]
        del self._orders[command.order_id]
        self._release(order.command, order.remaining_quantity)
        self._command_event(command, EventKindV2.ORDER_CANCELLED, {"quantity": order.remaining_quantity})
        self._assert_conservation()
        return True

    def replace(self, command: ReplaceOrderCommandV2) -> tuple[TradeV2, ...]:
        """Apply a native replace while preserving priority only for a size reduction at the same price."""
        self._validate_replace(command)
        self.kernel.admit_replace(command)
        return self._replace_after_admission(command)

    def replace_command(self, command: ReplaceOrderCommandV2) -> tuple[bool, tuple[TradeV2, ...]]:
        """Process an idempotent replace request with an explicit rejection path."""
        self.kernel.admit_replace(command)
        try:
            return True, self._replace_after_admission(command)
        except OrderRejectedError as error:
            self._command_event(command, EventKindV2.REPLACE_REJECTED, {"reason": str(error)})
            return False, ()

    def _validate_replace(self, command: ReplaceOrderCommandV2) -> None:
        order = self._orders.get(command.order_id)
        if order is None:
            raise OrderRejectedError("unknown_resting_order")
        if order.command.account_id != command.account_id:
            raise OrderRejectedError("replace_not_owner")
        if self.session_state != SessionStateV2.OPEN:
            raise OrderRejectedError("session_closed")
        if order.command.instrument_id in self._halted_instruments:
            raise OrderRejectedError("instrument_halted")
        if command.account_id in self._killed_accounts:
            raise OrderRejectedError("kill_switch_enabled")

    def _replace_after_admission(self, command: ReplaceOrderCommandV2) -> tuple[TradeV2, ...]:
        self._validate_replace(command)
        order = self._orders[command.order_id]
        replacement = dataclass_replace(
            order.command,
            command_id=command.command_id,
            quantity=command.quantity,
            price_ticks=command.price_ticks,
            exchange_time_ns=command.exchange_time_ns,
            venue_sequence=command.venue_sequence,
        )
        self._validate_risk(replacement)
        if not self._can_reserve_replacement(order, replacement):
            raise OrderRejectedError("insufficient_available_resources_for_replace")
        same_price_reduction = (
            replacement.price_ticks == order.command.price_ticks
            and replacement.quantity <= order.remaining_quantity
        )
        if same_price_reduction:
            self._release(order.command, order.remaining_quantity - replacement.quantity)
            order.command = replacement
            order.remaining_quantity = replacement.quantity
            self._command_event(
                command,
                EventKindV2.ORDER_REPLACED,
                {
                    "quantity": replacement.quantity,
                    "price_ticks": replacement.price_ticks,
                    "priority_retained": True,
                },
            )
            self._assert_conservation()
            return ()
        self._remove_resting(order)
        self._release(order.command, order.remaining_quantity)
        self._reserve(replacement)
        incoming = RestingOrderV2(replacement, replacement.quantity)
        trades = self._match(incoming)
        if incoming.remaining_quantity:
            self._rest(incoming)
        self._command_event(
            command,
            EventKindV2.ORDER_REPLACED,
            {
                "quantity": replacement.quantity,
                "price_ticks": replacement.price_ticks,
                "priority_retained": False,
            },
        )
        self._assert_conservation()
        return tuple(trades)

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

    def _validate_risk(self, command: OrderCommandV2) -> None:
        limits = self._risk_limits[command.account_id]
        if limits.max_order_quantity is not None and command.quantity > limits.max_order_quantity:
            raise OrderRejectedError("risk_max_order_quantity")
        if limits.max_order_notional_cents is None:
            return
        if command.price_ticks is None:
            raise OrderRejectedError("risk_requires_price_protection")
        notional = command.price_ticks * self.tick_size_cents * command.quantity
        if notional > limits.max_order_notional_cents:
            raise OrderRejectedError("risk_max_order_notional")

    def _can_reserve_replacement(self, order: RestingOrderV2, replacement: OrderCommandV2) -> bool:
        account = self.accounts[order.command.account_id]
        if replacement.side == SideV2.SELL:
            return (
                account.available_position(replacement.instrument_id) + order.remaining_quantity
                >= replacement.quantity
            )
        if order.command.price_ticks is None or replacement.price_ticks is None:
            raise ExchangeValidationError("cash replacement requires protected limit prices")
        released = self._maximum_cost(order.command.price_ticks, order.remaining_quantity)
        required = self._maximum_cost(replacement.price_ticks, replacement.quantity)
        return account.available_cash_cents() + released >= required

    def _release(self, command: OrderCommandV2, quantity: int) -> None:
        account = self.accounts[command.account_id]
        if command.side == SideV2.SELL:
            account.reserved_positions[command.instrument_id] -= quantity
            if account.reserved_positions[command.instrument_id] == 0:
                del account.reserved_positions[command.instrument_id]
        else:
            if command.price_ticks is None:
                raise ExchangeValidationError("buy reservation requires a protected limit price")
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
        if order.command.price_ticks is None:
            raise ExchangeValidationError("only protected limit orders may rest")
        self._levels(order.command.instrument_id, order.command.side).setdefault(
            order.command.price_ticks, []
        ).append(order.command.order_id)
        self._orders[order.command.order_id] = order

    def _remove_resting(self, order: RestingOrderV2) -> None:
        level = self._levels(order.command.instrument_id, order.command.side)[order.command.price_ticks or 0]
        level.remove(order.command.order_id)
        if not level:
            del self._levels(order.command.instrument_id, order.command.side)[order.command.price_ticks or 0]
        del self._orders[order.command.order_id]

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
        if command.price_ticks is None:
            raise ExchangeValidationError("limit order requires a protected limit price")
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
            OrderEventV2(
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

    def _control_event(
        self, kind: EventKindV2, target: str, exchange_time_ns: int, venue_sequence: int
    ) -> None:
        self._sequence += 1
        self.kernel.ledger.append(
            OrderEventV2(
                event_id=f"control-{self._sequence:020d}",
                kind=kind,
                exchange_time_ns=exchange_time_ns,
                venue_sequence=venue_sequence,
                event_priority=15,
                command_id=f"control:{kind.value}:{target}",
                order_id=f"control:{target}",
                payload={"target": target},
            )
        )

    def _command_event(
        self,
        command: CancelOrderCommandV2 | ReplaceOrderCommandV2,
        kind: EventKindV2,
        payload: dict[str, object],
    ) -> None:
        self._sequence += 1
        self.kernel.ledger.append(
            OrderEventV2(
                event_id=f"match-{self._sequence:020d}",
                kind=kind,
                exchange_time_ns=command.exchange_time_ns,
                venue_sequence=command.venue_sequence,
                event_priority=20,
                command_id=command.command_id,
                order_id=command.order_id,
                payload={"orig_order_id": command.order_id, **payload},
            )
        )

    def _reject(self, command: OrderCommandV2, reason: str) -> None:
        self._event(command, EventKindV2.ORDER_REJECTED, {"reason": reason})

    def _assert_conservation(self) -> None:
        if any(account.cash_cents < account.reserved_cash_cents for account in self.accounts.values()):
            raise ExchangeInvariantError("reservation exceeds cash")
        if any(
            account.available_position(symbol) < 0
            for account in self.accounts.values()
            for symbol in account.positions
        ):
            raise ExchangeInvariantError("reservation exceeds position")
