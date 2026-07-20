"""Deterministic V2 exchange execution for sealed generated worlds.

The runner is evaluator-owned.  It never passes a generated-world object,
seed, family label, regime, or ledger to the strategy decision port.  Strategy
responses must already be durably recorded by the isolated runtime before the
resulting command is admitted to the exchange.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from app.exchange.v2 import (
    CancelOrderCommandV2,
    EventKernelV2,
    OrderCommandV2,
    OrderTypeV2,
    ReplaceOrderCommandV2,
    RunManifestV2,
    SideV2,
    TimeInForceV2,
)
from app.exchange.v2_matching import AccountRiskLimitsV2, AccountStateV2, MatchingExchangeV2, TradeV2
from app.generators.v1 import GeneratedWorldV1
from app.strategy_protocol import StrategyActionV2, StrategyObservationV2, StrategyOpenOrderV2
from app.strategy_runtime import StrategyResponseRecordV1

from .sealed_v1 import PrimaryWorldExecutionV1


class SealedV2RunnerError(ValueError):
    """Fail-closed error for an invalid strategy binding or execution configuration."""


class StrategyDecisionPortV1(Protocol):
    """Durable, isolated strategy session boundary used by the sealed runner."""

    def decide(self, observation: dict[str, Any]) -> StrategyResponseRecordV1: ...


class StrategyDecisionPortFactoryV1(Protocol):
    """Build a fresh isolated strategy port for one hidden world."""

    def __call__(self) -> StrategyDecisionPortV1: ...


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SealedV2RunnerConfigV1:
    """Declared cash-like venue parameters for one sealed strategy execution."""

    initial_cash_cents: int = 10_000_000_000
    initial_position_per_instrument: int = 100_000
    external_liquidity_quantity: int = 100_000
    max_strategy_order_quantity: int = 10_000
    market_order_collar_bps: int = 500
    tick_size_cents: int = 1
    maker_fee_bps: int = 0
    taker_fee_bps: int = 0

    def __post_init__(self) -> None:
        if (
            min(
                self.initial_cash_cents,
                self.initial_position_per_instrument,
                self.external_liquidity_quantity,
                self.max_strategy_order_quantity,
                self.market_order_collar_bps,
                self.maker_fee_bps,
                self.taker_fee_bps,
            )
            < 0
            or self.tick_size_cents < 1
        ):
            raise SealedV2RunnerError(
                "sealed V2 runner configuration must be non-negative with a positive tick"
            )
        if self.external_liquidity_quantity < 1 or self.max_strategy_order_quantity < 1:
            raise SealedV2RunnerError(
                "sealed V2 runner requires positive liquidity and strategy order limits"
            )
        if self.market_order_collar_bps > 10_000:
            raise SealedV2RunnerError("market order collar cannot exceed 100 percent")


class SealedV2WorldRunnerV1:
    """Run one generated world through V2 with a strategy port bound to its frozen digest."""

    def __init__(
        self,
        strategy_port: StrategyDecisionPortV1,
        *,
        config: SealedV2RunnerConfigV1 | None = None,
    ) -> None:
        self.strategy_port = strategy_port
        self.config = config or SealedV2RunnerConfigV1()

    def run(self, world: GeneratedWorldV1, manifest: RunManifestV2) -> PrimaryWorldExecutionV1:
        if not world.events:
            raise SealedV2RunnerError("sealed V2 runner requires a non-empty generated event stream")
        instruments = tuple(sorted({event.instrument_id for event in world.events}))
        exchange = MatchingExchangeV2(
            EventKernelV2(manifest),
            tick_size_cents=self.config.tick_size_cents,
            maker_fee_bps=self.config.maker_fee_bps,
            taker_fee_bps=self.config.taker_fee_bps,
        )
        strategy_account = "strategy"
        initial_positions = {
            instrument: self.config.initial_position_per_instrument for instrument in instruments
        }
        exchange.register(
            AccountStateV2(strategy_account, self.config.initial_cash_cents, dict(initial_positions)),
            risk_limits=AccountRiskLimitsV2(max_order_quantity=self.config.max_strategy_order_quantity),
        )
        # Separate accounts prevent exogenous bid/ask flow from self-trading solely due to identity.
        exchange.register(
            AccountStateV2("liquidity_buy", self.config.initial_cash_cents, dict(initial_positions))
        )
        exchange.register(
            AccountStateV2("liquidity_sell", self.config.initial_cash_cents, dict(initial_positions))
        )
        initial_mark: dict[str, int] = {}
        latest_mark: dict[str, int] = {}
        strategy_trade_quantity = 0
        strategy_trade_count = 0
        strategy_order_count = 0
        strategy_cancel_count = 0
        strategy_replace_count = 0
        strategy_rejection_count = 0
        response_digests: list[str] = []
        observed_strategy_order_ids: set[str] = set()
        events = tuple(sorted(world.events, key=lambda item: item.exchange_time_ns))
        for step, event in enumerate(events):
            initial_mark.setdefault(event.instrument_id, event.price_ticks)
            latest_mark[event.instrument_id] = event.price_ticks
            if event.side not in ("buy", "sell"):
                raise SealedV2RunnerError("generated execution flow requires a declared buy or sell side")
            external_side = SideV2(event.side)
            external_account = "liquidity_buy" if external_side == SideV2.BUY else "liquidity_sell"
            exchange.submit(
                OrderCommandV2(
                    command_id=f"liquidity-command-{step:020d}",
                    order_id=f"liquidity-order-{step:020d}",
                    account_id=external_account,
                    instrument_id=event.instrument_id,
                    side=external_side,
                    order_type=OrderTypeV2.LIMIT,
                    quantity=min(event.quantity, self.config.external_liquidity_quantity),
                    price_ticks=event.price_ticks,
                    exchange_time_ns=event.exchange_time_ns,
                    venue_sequence=step * 10,
                )
            )
            observation = self._observation(
                exchange,
                manifest,
                event.instrument_id,
                step,
                cast(Literal["buy", "sell"], event.side),
                event.price_ticks,
                event.quantity,
                exchange.accounts[strategy_account].positions.get(event.instrument_id, 0),
            )
            record = self.strategy_port.decide(observation)
            action = self._verified_action(record, observation, manifest.strategy_artifact_digest)
            response_digests.append(record.response_digest)
            observed_strategy_order_ids.update(item["order_id"] for item in observation["open_orders"])
            before = len(exchange.kernel.ledger.events)
            trades: tuple[TradeV2, ...] = ()
            command_id: str | None = None
            command: OrderCommandV2 | CancelOrderCommandV2 | ReplaceOrderCommandV2
            if action.action_type == "submit":
                command = self._command_from_action(action, event, step)
                command_id = command.command_id
                trades = exchange.submit(command)
                strategy_order_count += 1
            elif action.action_type == "cancel":
                self._require_observed_order_id(action.order_id, observed_strategy_order_ids)
                command = CancelOrderCommandV2(
                    f"strategy-cancel-{step:020d}",
                    action.order_id or "",
                    strategy_account,
                    event.exchange_time_ns,
                    step * 10 + 5,
                )
                command_id = command.command_id
                exchange.cancel_command(command)
                strategy_cancel_count += 1
            elif action.action_type == "replace":
                self._require_observed_order_id(action.order_id, observed_strategy_order_ids)
                command = ReplaceOrderCommandV2(
                    f"strategy-replace-{step:020d}",
                    action.order_id or "",
                    strategy_account,
                    event.exchange_time_ns,
                    step * 10 + 5,
                    action.quantity,
                    action.limit_price_ticks or 0,
                )
                command_id = command.command_id
                _, trades = exchange.replace_command(command)
                strategy_replace_count += 1
            if action.action_type == "hold":
                continue
            strategy_trade_quantity += sum(
                trade.quantity
                for trade in trades
                if strategy_account in (trade.buyer_account_id, trade.seller_account_id)
            )
            strategy_trade_count += sum(
                1 for trade in trades if strategy_account in (trade.buyer_account_id, trade.seller_account_id)
            )
            if command_id is not None:
                strategy_rejection_count += sum(
                    1
                    for ledger_event in exchange.kernel.ledger.events[before:]
                    if ledger_event.kind.value in {"order_rejected", "cancel_rejected", "replace_rejected"}
                    and ledger_event.command_id == command_id
                )
        final_time = events[-1].exchange_time_ns + 1
        exchange.close_session(exchange_time_ns=final_time, venue_sequence=len(events) * 10 + 9)
        account = exchange.accounts[strategy_account]
        initial_value = self.config.initial_cash_cents + sum(
            initial_positions[instrument] * initial_mark[instrument] * self.config.tick_size_cents
            for instrument in instruments
        )
        final_value = account.cash_cents + sum(
            account.positions.get(instrument, 0) * latest_mark[instrument] * self.config.tick_size_cents
            for instrument in instruments
        )
        return PrimaryWorldExecutionV1(
            {
                "marked_to_market_pnl_cents": float(final_value - initial_value),
                "strategy_filled_quantity": float(strategy_trade_quantity),
                "strategy_trade_count": float(strategy_trade_count),
                "strategy_order_count": float(strategy_order_count),
                "strategy_cancel_count": float(strategy_cancel_count),
                "strategy_replace_count": float(strategy_replace_count),
                "strategy_rejection_count": float(strategy_rejection_count),
                "absolute_inventory_quantity": float(
                    sum(abs(account.positions.get(instrument, 0)) for instrument in instruments)
                ),
                "strategy_response_digest_count": float(len(response_digests)),
            },
            exchange.kernel.ledger.digest,
            _digest(response_digests),
        )

    def _observation(
        self,
        exchange: MatchingExchangeV2,
        manifest: RunManifestV2,
        instrument_id: str,
        step: int,
        side: Literal["buy", "sell"],
        mid_ticks: int,
        observed_volume: int,
        inventory: int,
    ) -> dict[str, Any]:
        bid, ask = self._best_quote(exchange, instrument_id)
        spread_bps = 0.0 if bid is None or ask is None else (ask - bid) * 10_000 / mid_ticks
        open_orders = tuple(
            StrategyOpenOrderV2(
                order_id=item.order_id,
                side=item.side.value,
                remaining_quantity=item.remaining_quantity,
                limit_price_ticks=item.limit_price_ticks,
            )
            for item in exchange.open_orders_for("strategy", instrument_id)
        )
        return StrategyObservationV2(
            session_id=f"sealed-{manifest.campaign_commitment[:24]}",
            step=step,
            symbol=instrument_id,
            side=side,
            mid_ticks=mid_ticks,
            best_bid_ticks=bid,
            best_ask_ticks=ask,
            spread_bps=spread_bps,
            observed_volume=observed_volume,
            inventory=inventory,
            remaining_quantity=0,
            exchange_latency_profile="normal",
            intervention_active=False,
            open_orders=open_orders,
        ).model_dump(mode="json")

    @staticmethod
    def _best_quote(exchange: MatchingExchangeV2, instrument_id: str) -> tuple[int | None, int | None]:
        return exchange.best_quote(instrument_id)

    @staticmethod
    def _verified_action(
        record: StrategyResponseRecordV1,
        observation: dict[str, Any],
        artifact_digest: str,
    ) -> StrategyActionV2:
        request_digest = _digest(observation)
        expected_idempotency = _digest({"artifact": artifact_digest, "request": request_digest})
        if (
            record.artifact_digest != artifact_digest
            or record.request_digest != request_digest
            or record.idempotency_key != expected_idempotency
            or record.response_digest != _digest(record.action)
        ):
            raise SealedV2RunnerError(
                "strategy response record does not bind to the frozen artifact and observation"
            )
        try:
            return StrategyActionV2.model_validate(record.action)
        except ValueError as error:
            raise SealedV2RunnerError(
                "sealed V2 execution requires a strategy action protocol version 2.0"
            ) from error

    def _command_from_action(self, action: StrategyActionV2, event: Any, step: int) -> OrderCommandV2:
        if action.action_type != "submit" or action.side is None or action.order_type is None:
            raise SealedV2RunnerError("only validated submit actions may create V2 orders")
        side = SideV2(action.side)
        if action.order_type == "limit":
            assert action.limit_price_ticks is not None
            price_ticks = action.limit_price_ticks
            time_in_force = TimeInForceV2.DAY
        else:
            multiplier = 10_000 + self.config.market_order_collar_bps
            if side == SideV2.SELL:
                multiplier = 10_000 - self.config.market_order_collar_bps
            rounding = math.ceil if side == SideV2.BUY else math.floor
            price_ticks = max(1, rounding(event.price_ticks * multiplier / 10_000))
            time_in_force = TimeInForceV2.IOC
        return OrderCommandV2(
            command_id=f"strategy-command-{step:020d}",
            order_id=f"strategy-order-{step:020d}",
            account_id="strategy",
            instrument_id=event.instrument_id,
            side=side,
            order_type=OrderTypeV2.LIMIT,
            quantity=action.quantity,
            price_ticks=price_ticks,
            time_in_force=time_in_force,
            exchange_time_ns=event.exchange_time_ns,
            venue_sequence=step * 10 + 5,
        )

    @staticmethod
    def _require_observed_order_id(order_id: str | None, observed_order_ids: set[str]) -> None:
        if order_id is None or order_id not in observed_order_ids:
            raise SealedV2RunnerError(
                "strategy lifecycle action may reference only a previously observed own order ID"
            )


class IsolatedSealedV2WorldRunnerV1:
    """Reset strategy process state between hidden worlds while keeping each world streaming."""

    def __init__(
        self,
        strategy_port_factory: StrategyDecisionPortFactoryV1,
        *,
        config: SealedV2RunnerConfigV1 | None = None,
    ) -> None:
        self.strategy_port_factory = strategy_port_factory
        self.config = config

    def run(self, world: GeneratedWorldV1, manifest: RunManifestV2) -> PrimaryWorldExecutionV1:
        strategy_port = self.strategy_port_factory()
        try:
            return SealedV2WorldRunnerV1(strategy_port, config=self.config).run(world, manifest)
        finally:
            close = getattr(strategy_port, "close", None)
            if callable(close):
                close()
