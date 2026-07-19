from __future__ import annotations

import hashlib
import heapq
import json
import random
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from app.agents.behaviors import AgentContext, ExecutionAgent, MarketMaker, build_agents
from app.exchange import Account, CancelRequest, Exchange, Order, OrderType, Side
from app.orderflow import QueueReactiveProvider, RuleBasedProvider
from app.schemas import WorldSpec
from app.strategy_protocol import StrategyActionV1

MESSAGE_STEP_MS = 20
ExecutionDecider = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class LatencyProfile:
    """Explicit message-lifecycle latency used by the coarse discrete-event engine."""

    feed_ms: int
    decision_ms: int
    order_entry_ms: int
    cancel_ms: int


@dataclass
class SimulationResult:
    spec_hash: str
    result_hash: str
    seed: int
    timeline: list[dict]
    orders: list[dict]
    trades: list[dict]
    cancels: list[dict]
    events: list[dict]
    agent_states: list[dict]
    strategy_steps: list[dict]
    strategy_observations: list[dict]
    latency_profile: dict
    summary: dict
    runtime_ms: float

    def to_dict(self) -> dict:
        return asdict(self)


def _latency_profile(decision_ms: int, profile: str) -> LatencyProfile:
    feed_ms, order_entry_ms, cancel_ms = {
        "low": (0, 0, 0),
        "normal": (2, 2, 2),
        "high": (20, 40, 20),
    }[profile]
    return LatencyProfile(feed_ms, decision_ms, order_entry_ms, cancel_ms)


def _order_timing(step: int, latency: LatencyProfile) -> dict[str, int]:
    market_event_time = step * MESSAGE_STEP_MS
    publication_time = market_event_time + latency.feed_ms
    observation_time = publication_time
    decision_time = observation_time + latency.decision_ms
    submission_time = decision_time
    arrival_time = submission_time + latency.order_entry_ms
    return {
        "market_event_time_ms": market_event_time,
        "publication_time_ms": publication_time,
        "observation_time_ms": observation_time,
        "decision_time_ms": decision_time,
        "submission_time_ms": submission_time,
        "exchange_arrival_time_ms": arrival_time,
        "exchange_arrival_step": arrival_time // MESSAGE_STEP_MS,
    }


def _stamp_order(order: Order, step: int, latency: LatencyProfile) -> None:
    for field, value in _order_timing(step, latency).items():
        setattr(order, field, value)


def _strategy_order_id(trade: dict[str, Any], strategy_id: str) -> str | None:
    if trade.get("maker_id") == strategy_id:
        return str(trade["maker_order_id"])
    if trade.get("taker_id") == strategy_id:
        return str(trade["taker_order_id"])
    return None


def _build_strategy_steps(
    *,
    steps: int,
    target_quantity: int,
    target_symbol: str,
    side: Side,
    strategy_id: str,
    arrival_price: int,
    orders: list[dict],
    cancels: list[dict],
    trades: list[dict],
    observations: list[dict],
    agent_states: list[dict],
) -> list[dict]:
    strategy_orders = [row for row in orders if row["agent_id"] == strategy_id]
    submitted: dict[int, list[tuple[str, int]]] = defaultdict(list)
    rejected: dict[int, list[tuple[str, int]]] = defaultdict(list)
    expired: dict[int, list[tuple[str, int]]] = defaultdict(list)
    cancelled: dict[int, list[tuple[str, int]]] = defaultdict(list)
    filled: dict[int, list[tuple[str, int]]] = defaultdict(list)
    market_volume: dict[int, int] = defaultdict(int)
    shortfall_numerator: dict[int, float] = defaultdict(float)
    direction = 1 if side == Side.BUY else -1

    for order in strategy_orders:
        order_id = str(order["order_id"])
        submitted[int(order["submitted_step"])].append((order_id, int(order["quantity"])))
        if order.get("arrival_step") is not None:
            arrival_step = int(order["arrival_step"])
            if order.get("rejected_quantity"):
                rejected[arrival_step].append((order_id, int(order["rejected_quantity"])))
            if order.get("expired_quantity"):
                expired[arrival_step].append((order_id, int(order["expired_quantity"])))
    for cancel in cancels:
        if cancel["agent_id"] == strategy_id:
            cancelled[int(cancel["effective_step"])].append(
                (str(cancel["order_id"]), int(cancel["cancelled_quantity"]))
            )
    strategy_filled_total = 0
    for trade in trades:
        step = int(trade["step"])
        if trade["symbol"] == target_symbol:
            market_volume[step] += int(trade["quantity"])
        strategy_order_id = _strategy_order_id(trade, strategy_id)
        if strategy_order_id is not None:
            quantity = int(trade["quantity"])
            strategy_filled_total += quantity
            filled[step].append((strategy_order_id, quantity))
            shortfall_numerator[step] += (
                direction * (float(trade["price_ticks"]) / arrival_price - 1) * 10_000 * quantity
            )

    observation_by_step = {int(row["step"]): row for row in observations}
    inventory_by_step = {
        int(row["step"]): int(row["inventory"].get(target_symbol, 0))
        for row in agent_states
        if row["agent_id"] == strategy_id
    }
    active_by_order: dict[str, int] = {}
    cumulative_submitted = 0
    cumulative_filled = 0
    cumulative_cancelled = 0
    cumulative_expired = 0
    cumulative_rejected = 0
    rows: list[dict] = []
    for step in range(steps):
        for order_id, quantity in submitted.get(step, []):
            active_by_order[order_id] = active_by_order.get(order_id, 0) + quantity
            cumulative_submitted += quantity
        step_filled = sum(quantity for _, quantity in filled.get(step, []))
        step_cancelled = sum(quantity for _, quantity in cancelled.get(step, []))
        step_expired = sum(quantity for _, quantity in expired.get(step, []))
        step_rejected = sum(quantity for _, quantity in rejected.get(step, []))
        for events in (
            filled.get(step, []),
            cancelled.get(step, []),
            expired.get(step, []),
            rejected.get(step, []),
        ):
            for order_id, quantity in events:
                active_by_order[order_id] = active_by_order.get(order_id, 0) - quantity
                if active_by_order[order_id] < 0:
                    raise AssertionError(f"negative active child quantity for {order_id}")
        cumulative_filled += step_filled
        cumulative_cancelled += step_cancelled
        cumulative_expired += step_expired
        cumulative_rejected += step_rejected
        active_quantity = sum(active_by_order.values())
        remaining_parent = target_quantity - cumulative_filled
        expected_inventory = direction * cumulative_filled
        inventory = inventory_by_step.get(step, expected_inventory)
        child_ties = cumulative_submitted == (
            cumulative_filled
            + cumulative_cancelled
            + cumulative_expired
            + cumulative_rejected
            + active_quantity
        )
        cancelled_terminal_quantity = 0
        parent_ties = target_quantity == cumulative_filled + cancelled_terminal_quantity + remaining_parent
        inventory_ties = inventory == expected_inventory
        if not child_ties or not parent_ties or not inventory_ties or active_quantity > remaining_parent:
            raise AssertionError(f"strategy accounting invariant failed at step {step}")
        volume = market_volume.get(step, 0)
        rows.append(
            {
                "step": step,
                "strategy_submitted_quantity": sum(quantity for _, quantity in submitted.get(step, [])),
                "strategy_cancelled_quantity": step_cancelled,
                "strategy_expired_quantity": step_expired,
                "strategy_rejected_quantity": step_rejected,
                "strategy_filled_quantity": step_filled,
                "strategy_active_quantity": active_quantity,
                "active_child_order_count": sum(quantity > 0 for quantity in active_by_order.values()),
                "market_volume": volume,
                "observed_volume": int(observation_by_step.get(step, {}).get("observed_volume", 0)),
                "remaining_parent_quantity": remaining_parent,
                "cancelled_terminal_quantity": cancelled_terminal_quantity,
                "filled_inventory": cumulative_filled,
                "account_inventory": inventory,
                "participation": step_filled / volume if volume else 0.0,
                "shortfall_contribution_bps": (
                    shortfall_numerator.get(step, 0.0) / strategy_filled_total
                    if strategy_filled_total
                    else 0.0
                ),
                "child_order_accounting_ties": child_ties,
                "parent_inventory_accounting_ties": parent_ties,
                "strategy_inventory_accounting_ties": inventory_ties,
            }
        )
    return rows


def _orders_from_adapter_action(
    agent: ExecutionAgent, context: AgentContext, action: dict[str, Any]
) -> list[Order]:
    parsed = StrategyActionV1.model_validate(action)
    if parsed.action_type == "hold":
        return []
    assert parsed.side is not None
    side = Side(parsed.side)
    if parsed.action_type == "limit":
        return [
            Order(
                agent.next_id(),
                agent.agent_id,
                context.symbol,
                side,
                OrderType.LIMIT,
                parsed.quantity,
                context.step,
                parsed.limit_price_ticks,
            )
        ]
    order = agent.market(context, side, parsed.quantity)
    return [order] if order else []


def run_simulation(spec: WorldSpec, *, execution_decider: ExecutionDecider | None = None) -> SimulationResult:
    started = time.perf_counter()
    rng = random.Random(spec.seed)
    exchange = Exchange([asset.ticker for asset in spec.assets], spec.exchange)
    provider = (
        QueueReactiveProvider(spec) if spec.order_flow_provider == "queue_reactive" else RuleBasedProvider()
    )
    agents = build_agents(spec.agents.populations, spec)
    agent_map = {agent.agent_id: agent for agent in agents}
    execution = next(agent for agent in agents if isinstance(agent, ExecutionAgent))
    for agent in agents:
        exchange.register(
            Account(agent.agent_id, agent.capital_cents, {asset.ticker: 0 for asset in spec.assets})
        )
    for account_id in provider.account_ids:
        exchange.register(Account(account_id, 10_000_000_000, {asset.ticker: 0 for asset in spec.assets}))
    intervention_seller_id = "intervention-forced-seller"
    exchange.register(
        Account(intervention_seller_id, 10_000_000_000, {asset.ticker: 1_000_000 for asset in spec.assets})
    )

    fundamentals = {asset.ticker: asset.initial_fundamental_value_ticks for asset in spec.assets}
    prices = {asset.ticker: [asset.initial_price_ticks] for asset in spec.assets}
    liquidity_multiplier = spec.interventions.displayed_depth_multiplier
    pending: list[tuple[int, int, int, str, Any]] = []
    pending_cancel_ids: set[str] = set()
    pending_sequence = 0
    events_log: list[dict] = []
    timeline: list[dict] = []
    agent_states: list[dict] = []
    strategy_observations: list[dict] = []
    trade_cursor = 0
    initial_cash_cents = exchange.total_cash_cents()
    initial_inventory = {symbol: exchange.total_inventory(symbol) for symbol in exchange.books}
    event_map: dict[int, list] = {}
    for event in spec.events:
        event_map.setdefault(event.simulation_step, []).append(event)

    def apply_execution_fills(trades: list[Any]) -> None:
        execution.executed_quantity += sum(
            trade.quantity
            for trade in trades
            if trade.buyer_id == execution.agent_id or trade.seller_id == execution.agent_id
        )

    def submit_to_exchange(order: Order, step: int) -> list[Any]:
        trades = exchange.submit(order, step)
        apply_execution_fills(trades)
        agent = agent_map.get(order.agent_id)
        if agent is not None and order.remaining and order.order_type == OrderType.LIMIT:
            agent.resting_order_ids.add(order.order_id)
        return trades

    def active_strategy_quantity() -> int:
        queued = sum(
            int(message.remaining or 0)
            for _, _, _, kind, message in pending
            if kind == "order" and message.agent_id == execution.agent_id
        )
        resting = sum(
            int(order.remaining or 0)
            for book in exchange.books.values()
            for order in book.orders.values()
            if order.agent_id == execution.agent_id
        )
        return queued + resting

    def schedule_order(order: Order, step: int, latency: LatencyProfile) -> None:
        nonlocal pending_sequence
        _stamp_order(order, step, latency)
        exchange.record_submission(order)
        arrival_step = int(order.exchange_arrival_step or step)
        agent = agent_map.get(order.agent_id)
        cancel_after_ms = int(agent.parameters.get("cancel_after_ms", 10_000)) if agent else 10_000
        request_time = int(order.submission_time_ms or step * MESSAGE_STEP_MS) + cancel_after_ms
        cancel_effective_time = request_time + latency.cancel_ms
        if cancel_effective_time < int(order.exchange_arrival_time_ms or 0):
            exchange.cancel_pending(
                order,
                request_step=request_time // MESSAGE_STEP_MS,
                request_time_ms=request_time,
                effective_step=cancel_effective_time // MESSAGE_STEP_MS,
                effective_time_ms=cancel_effective_time,
            )
            return
        if arrival_step <= step:
            submit_to_exchange(order, step)
            return
        pending_sequence += 1
        heapq.heappush(
            pending,
            (
                arrival_step,
                int(order.exchange_arrival_time_ms or arrival_step * MESSAGE_STEP_MS),
                pending_sequence,
                "order",
                order,
            ),
        )

    def request_cancel(agent: Any, symbol: str, order_id: str, step: int) -> None:
        nonlocal pending_sequence
        latency = _latency_profile(agent.latency_ms, spec.exchange.latency_profile)
        request_time = step * MESSAGE_STEP_MS
        effective_time = request_time + latency.cancel_ms
        effective_step = effective_time // MESSAGE_STEP_MS
        request = CancelRequest(
            order_id,
            agent.agent_id,
            step,
            request_time,
            effective_step,
            effective_time,
        )
        pending_cancel_ids.add(order_id)
        if effective_step <= step:
            try:
                exchange.cancel(request, symbol)
            finally:
                pending_cancel_ids.discard(order_id)
                agent.resting_order_ids.discard(order_id)
            return
        pending_sequence += 1
        heapq.heappush(
            pending,
            (effective_step, effective_time, pending_sequence, "cancel", (request, symbol)),
        )

    # Prime persistent books before the first strategy slice.
    for agent in agents:
        if isinstance(agent, MarketMaker):
            for asset in spec.assets:
                context = AgentContext(
                    0,
                    asset.ticker,
                    fundamentals[asset.ticker],
                    prices[asset.ticker][-1],
                    prices[asset.ticker],
                    0,
                    liquidity_multiplier,
                )
                for order in agent.decide(context, rng, 0):
                    _stamp_order(order, 0, LatencyProfile(0, 0, 0, 0))
                    submit_to_exchange(order, 0)

    arrival_price = prices[spec.experiment.target_asset][-1]
    forced_remaining = spec.interventions.forced_seller_quantity
    forced_start = max(1, spec.clock.steps // 2)
    for step in range(spec.clock.steps):
        for event in event_map.get(step, []):
            if event.asset:
                fundamentals[event.asset] = max(
                    1, round(fundamentals[event.asset] * (1 + event.fundamental_effect_pct / 100))
                )
            else:
                for symbol in fundamentals:
                    fundamentals[symbol] = max(
                        1, round(fundamentals[symbol] * (1 + event.fundamental_effect_pct / 100))
                    )
            liquidity_multiplier *= event.liquidity_effect
            events_log.append(
                {
                    **event.model_dump(mode="json"),
                    "step": step,
                    "market_event_time_ms": step * MESSAGE_STEP_MS,
                }
            )

        factor = rng.gauss(
            0,
            {"low": 0.0002, "normal": 0.0005, "elevated": 0.001, "crisis": 0.002}[
                spec.macro.volatility_regime
            ],
        )
        for asset in spec.assets:
            previous = fundamentals[asset.ticker]
            specific = rng.gauss(0, asset.idiosyncratic_volatility)
            anchor = asset.initial_fundamental_value_ticks
            change = factor * asset.macro_beta * spec.macro.common_factor_strength + specific
            fundamentals[asset.ticker] = max(
                1, round(previous * (1 + change) + asset.mean_reversion * (anchor - previous))
            )

        while pending and pending[0][0] <= step:
            _, _, _, kind, message = heapq.heappop(pending)
            if kind == "cancel":
                request, symbol = message
                agent = agent_map[request.agent_id]
                try:
                    exchange.cancel(request, symbol)
                except (KeyError, PermissionError):
                    pass
                finally:
                    pending_cancel_ids.discard(request.order_id)
                    agent.resting_order_ids.discard(request.order_id)
                continue
            order = message
            try:
                submit_to_exchange(order, step)
            except RuntimeError:
                # A delayed order can arrive during a configured exchange halt.
                continue

        for asset in spec.assets:
            for action in provider.actions(step, asset.ticker, exchange, rng):
                try:
                    if action.cancel is not None:
                        exchange.cancel(action.cancel, action.symbol)
                    elif action.order is not None:
                        submit_to_exchange(action.order, step)
                    events_log.append(
                        {
                            "event_id": f"order-flow-{step}-{len(events_log) + 1}",
                            "step": step,
                            "simulation_step": step,
                            "scope": "asset",
                            "asset": asset.ticker,
                            "type": "order_flow",
                            "order_flow_event_type": action.event_type,
                            "backoff_level": action.backoff_level,
                            "world_type": spec.world_type,
                            "market_event_time_ms": step * MESSAGE_STEP_MS,
                        }
                    )
                except (KeyError, PermissionError, RuntimeError):
                    continue

        if forced_remaining > 0 and step >= forced_start:
            slice_quantity = min(forced_remaining, max(10, spec.interventions.forced_seller_quantity // 12))
            slice_quantity -= slice_quantity % spec.exchange.lot_size
            if slice_quantity > 0:
                forced_order = Order(
                    f"INTERVENTION-SELL-{step:05d}",
                    intervention_seller_id,
                    spec.experiment.target_asset,
                    Side.SELL,
                    OrderType.MARKET,
                    slice_quantity,
                    step,
                )
                try:
                    submit_to_exchange(forced_order, step)
                    forced_remaining -= slice_quantity
                except RuntimeError:
                    pass

        observed_volume = sum(row["quantity"] for row in exchange.trade_log[trade_cursor:])
        for agent in agents:
            # Market makers replace only their own live quotes; cancel remains legal during halts.
            if isinstance(agent, MarketMaker):
                for symbol in list(exchange.books):
                    for order_id in list(agent.resting_order_ids):
                        if order_id in exchange.books[symbol].orders and order_id not in pending_cancel_ids:
                            try:
                                request_cancel(agent, symbol, order_id, step)
                            except (KeyError, PermissionError):
                                # The order may already have filled or been canceled.
                                agent.resting_order_ids.discard(order_id)
            for asset in spec.assets:
                book = exchange.books[asset.ticker]
                snapshot = book.snapshot()
                bid, ask = snapshot["best_bid_ticks"], snapshot["best_ask_ticks"]
                mid = (
                    round((bid + ask) / 2)
                    if bid is not None and ask is not None
                    else (book.last_price_ticks or prices[asset.ticker][-1])
                )
                context = AgentContext(
                    step,
                    asset.ticker,
                    fundamentals[asset.ticker],
                    mid,
                    prices[asset.ticker],
                    observed_volume,
                    liquidity_multiplier,
                )
                latency = _latency_profile(agent.latency_ms, spec.exchange.latency_profile)
                if isinstance(agent, ExecutionAgent) and asset.ticker == spec.experiment.target_asset:
                    timing = _order_timing(step, latency)
                    strategy_observations.append(
                        {
                            "step": step,
                            "strategy_id": agent.agent_id,
                            "symbol": asset.ticker,
                            "observed_volume": observed_volume,
                            "best_bid_ticks": bid,
                            "best_ask_ticks": ask,
                            "mid_ticks": mid,
                            "market_event_time_ms": timing["market_event_time_ms"],
                            "publication_time_ms": timing["publication_time_ms"],
                            "observation_time_ms": timing["observation_time_ms"],
                            "decision_time_ms": timing["decision_time_ms"],
                        }
                    )
                inventory = exchange.accounts[agent.agent_id].inventory.get(asset.ticker, 0)
                spread_bps = (
                    (ask - bid) / max(1, mid) * 10_000
                    if bid is not None and ask is not None
                    else float("inf")
                )
                pause_for_halt = bool(agent.parameters.get("pause_during_halt", False)) and book.is_halted(
                    step
                )
                pause_for_spread = bool(
                    agent.parameters.get("pause_above_spread_limit", False)
                ) and spread_bps > float(agent.parameters.get("max_spread_bps", float("inf")))
                pause_for_stale_feed = latency.feed_ms > int(
                    agent.parameters.get("feed_latency_tolerance_ms", 10_000)
                )
                if isinstance(agent, ExecutionAgent) and asset.ticker == spec.experiment.target_asset:
                    strategy_observations[-1]["paused"] = bool(
                        pause_for_halt or pause_for_spread or pause_for_stale_feed
                    )
                    strategy_observations[-1]["pause_reasons"] = [
                        reason
                        for reason, active in (
                            ("exchange_halt", pause_for_halt),
                            ("spread_limit", pause_for_spread),
                            ("feed_latency_tolerance", pause_for_stale_feed),
                        )
                        if active
                    ]
                    strategy_observations[-1].update(
                        {
                            "inventory": inventory,
                            "remaining_quantity": max(0, agent.target_quantity - agent.executed_quantity),
                            "spread_bps": min(1_000_000.0, max(0.0, spread_bps)),
                            "exchange_latency_profile": spec.exchange.latency_profile,
                            "intervention_active": bool(
                                liquidity_multiplier < 1.0
                                or spec.macro.volatility_regime != "normal"
                                or spec.exchange.latency_profile == "high"
                            ),
                        }
                    )
                if pause_for_halt or pause_for_spread or pause_for_stale_feed:
                    decisions = []
                elif (
                    execution_decider is not None
                    and isinstance(agent, ExecutionAgent)
                    and asset.ticker == spec.experiment.target_asset
                ):
                    adapter_observation = {
                        **strategy_observations[-1],
                        "session_id": f"{spec.world_id}:{agent.agent_id}",
                    }
                    adapter_action = execution_decider(adapter_observation)
                    if isinstance(adapter_action, dict):
                        strategy_observations[-1]["adapter_action"] = StrategyActionV1.model_validate(
                            adapter_action
                        ).model_dump(mode="json")
                    decisions = _orders_from_adapter_action(agent, context, adapter_action)
                else:
                    decisions = agent.decide(context, rng, inventory)
                for order in decisions:
                    if isinstance(agent, ExecutionAgent):
                        active_quantity = active_strategy_quantity()
                        available = agent.target_quantity - agent.executed_quantity - active_quantity
                        if available <= 0:
                            continue
                        if execution_decider is None:
                            progress = step / max(1, spec.clock.steps - 1)
                            urgency_curve = str(agent.parameters.get("urgency_curve", "uniform"))
                            urgency_factor = {
                                "uniform": 1.0,
                                "front_loaded": 1.5 - progress,
                                "back_loaded": 0.5 + progress,
                                "adaptive": (
                                    1.25
                                    if spec.macro.volatility_regime != "normal"
                                    or spec.exchange.latency_profile == "high"
                                    else 1.0
                                ),
                            }.get(urgency_curve, 1.0)
                            adjusted = max(
                                spec.exchange.lot_size,
                                int(order.quantity * urgency_factor)
                                // spec.exchange.lot_size
                                * spec.exchange.lot_size,
                            )
                            buffer_steps = int(agent.parameters.get("completion_buffer_steps", 0))
                            steps_left = spec.clock.steps - step
                            adaptive_stress_active = (
                                liquidity_multiplier < 1.0
                                or spec.macro.volatility_regime != "normal"
                                or spec.exchange.latency_profile == "high"
                            )
                            if buffer_steps and adaptive_stress_active and steps_left <= buffer_steps:
                                completion_slice = (
                                    (available + steps_left - 1) // steps_left // spec.exchange.lot_size
                                ) * spec.exchange.lot_size
                                adjusted = max(adjusted, completion_slice)
                            if bool(agent.parameters.get("enforce_max_participation", False)):
                                max_participation = float(agent.parameters["max_participation"])
                                participation_budget = max(
                                    spec.exchange.lot_size,
                                    int(observed_volume * max_participation)
                                    // spec.exchange.lot_size
                                    * spec.exchange.lot_size,
                                )
                                if bool(agent.parameters.get("include_pending_in_budget", True)):
                                    participation_budget = max(
                                        spec.exchange.lot_size,
                                        participation_budget - active_quantity,
                                    )
                                adjusted = min(adjusted, participation_budget)
                        else:
                            adjusted = order.quantity
                        order.quantity = min(available, adjusted)
                        order.remaining = order.quantity
                        if order.quantity > available:
                            order.quantity = available
                            order.remaining = available
                    try:
                        schedule_order(order, step, latency)
                    except RuntimeError:
                        # Agent orders are rejected while a halt is active.
                        continue

        asset_states: dict[str, dict] = {}
        for asset in spec.assets:
            book = exchange.books[asset.ticker]
            snapshot = book.snapshot(spec.exchange.book_depth_levels)
            bid, ask = snapshot["best_bid_ticks"], snapshot["best_ask_ticks"]
            mid = (
                round((bid + ask) / 2)
                if bid is not None and ask is not None
                else (book.last_price_ticks or prices[asset.ticker][-1])
            )
            prices[asset.ticker].append(mid)
            reference = asset.initial_price_ticks
            if (
                book.last_price_ticks
                and abs(book.last_price_ticks / reference - 1) * 100 >= spec.exchange.circuit_breaker_pct
            ):
                book.halt(step, spec.exchange.halt_steps)
            asset_states[asset.ticker] = {
                "mid_ticks": mid,
                "fundamental_ticks": fundamentals[asset.ticker],
                "best_bid_ticks": bid,
                "best_ask_ticks": ask,
                "spread_ticks": ask - bid if bid is not None and ask is not None else None,
                "bid_depth": sum(level["quantity"] for level in snapshot["bids"]),
                "ask_depth": sum(level["quantity"] for level in snapshot["asks"]),
                "volume": sum(
                    row["quantity"]
                    for row in exchange.trade_log[trade_cursor:]
                    if row["symbol"] == asset.ticker
                ),
                "halted": book.is_halted(step),
                "book": snapshot,
            }
        cumulative_strategy_fills = sum(
            int(trade["quantity"])
            for trade in exchange.trade_log
            if trade["buyer_id"] == execution.agent_id or trade["seller_id"] == execution.agent_id
        )
        strategy_direction = 1 if execution.side == Side.BUY else -1
        strategy_inventory = exchange.accounts[execution.agent_id].inventory.get(
            spec.experiment.target_asset, 0
        )
        inventory_conservation = {
            symbol: exchange.total_inventory(symbol) == initial_inventory[symbol] for symbol in exchange.books
        }
        cash_conservation = exchange.total_cash_cents() == initial_cash_cents
        strategy_inventory_ties = strategy_inventory == strategy_direction * cumulative_strategy_fills
        parent_capacity_ties = (
            cumulative_strategy_fills + active_strategy_quantity() <= execution.target_quantity
        )
        if not all(inventory_conservation.values()) or not cash_conservation:
            raise AssertionError(f"exchange conservation invariant failed at step {step}")
        if (
            execution.executed_quantity != cumulative_strategy_fills
            or not strategy_inventory_ties
            or not parent_capacity_ties
        ):
            raise AssertionError(f"execution inventory invariant failed at step {step}")
        timeline.append(
            {
                "step": step,
                "asset_states": asset_states,
                "liquidity_multiplier": liquidity_multiplier,
                "events": [row for row in events_log if row["step"] == step],
                "accounting": {
                    "cash_conservation": cash_conservation,
                    "inventory_conservation": inventory_conservation,
                    "strategy_inventory_ties": strategy_inventory_ties,
                    "parent_capacity_ties": parent_capacity_ties,
                },
            }
        )
        trade_cursor = len(exchange.trade_log)
        for agent in agents:
            account = exchange.accounts[agent.agent_id]
            agent_states.append(
                {
                    "step": step,
                    "agent_id": agent.agent_id,
                    "agent_type": agent.agent_type,
                    "cash_cents": account.cash_cents,
                    "inventory": dict(account.inventory),
                }
            )

    exchange.finalize_order_log(spec.clock.steps - 1)
    strategy_trades = [
        trade
        for trade in exchange.trade_log
        if trade["buyer_id"] == execution.agent_id or trade["seller_id"] == execution.agent_id
    ]
    executed = sum(trade["quantity"] for trade in strategy_trades)
    if executed > spec.experiment.parent_order.quantity:
        raise AssertionError("execution fills exceed parent quantity")
    strategy_steps = _build_strategy_steps(
        steps=spec.clock.steps,
        target_quantity=spec.experiment.parent_order.quantity,
        target_symbol=spec.experiment.target_asset,
        side=execution.side,
        strategy_id=execution.agent_id,
        arrival_price=arrival_price,
        orders=exchange.order_log,
        cancels=exchange.cancel_log,
        trades=exchange.trade_log,
        observations=strategy_observations,
        agent_states=agent_states,
    )
    for frame, strategy_step in zip(timeline, strategy_steps, strict=True):
        frame["strategy"] = strategy_step
    notional_ticks = sum(trade["price_ticks"] * trade["quantity"] for trade in strategy_trades)
    average = notional_ticks / executed if executed else 0.0
    target_market_trades = [
        trade for trade in exchange.trade_log if trade["symbol"] == spec.experiment.target_asset
    ]
    market_quantity = sum(trade["quantity"] for trade in target_market_trades)
    market_vwap = (
        sum(trade["price_ticks"] * trade["quantity"] for trade in target_market_trades) / market_quantity
        if market_quantity
        else arrival_price
    )
    direction = 1 if spec.experiment.parent_order.side == "buy" else -1
    shortfall = direction * (average / arrival_price - 1) * 10_000 if executed else 0.0
    last_price = timeline[-1]["asset_states"][spec.experiment.target_asset]["mid_ticks"]
    mids = [frame["asset_states"][spec.experiment.target_asset]["mid_ticks"] for frame in timeline]
    temporary = direction * ((max(mids) if direction > 0 else min(mids)) / arrival_price - 1) * 10_000
    spreads = [frame["asset_states"][spec.experiment.target_asset]["spread_ticks"] for frame in timeline]
    valid_spreads = [spread for spread in spreads if spread is not None]
    max_loss = min(direction * (mid / arrival_price - 1) * 10_000 for mid in mids)
    summary = {
        "filled_quantity": executed,
        "fill_rate": executed / spec.experiment.parent_order.quantity,
        "average_execution_price_ticks": average,
        "arrival_price_ticks": arrival_price,
        "market_vwap_ticks": market_vwap,
        "vwap_slippage_bps": direction * (average / market_vwap - 1) * 10_000 if executed else 0.0,
        "implementation_shortfall_bps": shortfall,
        "slippage_bps": shortfall,
        "temporary_impact_bps": temporary,
        "spread_paid_bps": (sum(valid_spreads) / len(valid_spreads) / arrival_price * 10_000)
        if valid_spreads
        else 0.0,
        "adverse_selection_bps": direction * (last_price / average - 1) * 10_000 if executed else 0.0,
        "remaining_inventory": spec.experiment.parent_order.quantity - executed,
        "persistent_impact_bps": direction * (last_price / arrival_price - 1) * 10_000,
        "max_mark_to_market_loss_bps": max_loss,
        "final_price_ticks": last_price,
        "total_market_volume": sum(trade["quantity"] for trade in exchange.trade_log),
        "target_market_volume": market_quantity,
        "strategy_submitted_quantity": sum(
            int(row["quantity"]) for row in exchange.order_log if row["agent_id"] == execution.agent_id
        ),
        "strategy_cancelled_quantity": sum(
            int(row["cancelled_quantity"])
            for row in exchange.cancel_log
            if row["agent_id"] == execution.agent_id
        ),
        "strategy_active_quantity": strategy_steps[-1]["strategy_active_quantity"],
        "parent_inventory_accounting_ties": strategy_steps[-1]["parent_inventory_accounting_ties"],
        "child_order_accounting_ties": strategy_steps[-1]["child_order_accounting_ties"],
        "strategy_inventory_accounting_ties": strategy_steps[-1]["strategy_inventory_accounting_ties"],
        "market_disruption": max(0.0, temporary - shortfall),
        "fees_cents": exchange.fee_account_cents,
        "world_type": spec.world_type,
        "response_classification": (
            "imposed structural assumption"
            if spec.world_type == "structural_benchmark"
            else "observed emergent simulation output"
        ),
        "calibration_pack_id": spec.calibration_pack_id,
        "calibration_parameter_set_id": spec.calibration_parameter_set_id,
        "order_flow_provider": spec.order_flow_provider,
        "interventions": spec.interventions.model_dump(mode="json"),
    }
    latency_profile = {
        "profile_name": spec.exchange.latency_profile,
        **asdict(_latency_profile(execution.latency_ms, spec.exchange.latency_profile)),
        "message_step_ms": MESSAGE_STEP_MS,
    }
    deterministic = {
        "spec_hash": spec.specification_hash(),
        "timeline": timeline,
        "orders": exchange.order_log,
        "trades": exchange.trade_log,
        "cancels": exchange.cancel_log,
        "events": events_log,
        "strategy_steps": strategy_steps,
        "strategy_observations": strategy_observations,
        "latency_profile": latency_profile,
        "summary": summary,
    }
    result_hash = hashlib.sha256(
        json.dumps(deterministic, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return SimulationResult(
        spec_hash=spec.specification_hash(),
        result_hash=result_hash,
        seed=spec.seed,
        timeline=timeline,
        orders=exchange.order_log,
        trades=exchange.trade_log,
        cancels=exchange.cancel_log,
        events=events_log,
        agent_states=agent_states,
        strategy_steps=strategy_steps,
        strategy_observations=strategy_observations,
        latency_profile=latency_profile,
        summary=summary,
        runtime_ms=(time.perf_counter() - started) * 1_000,
    )
