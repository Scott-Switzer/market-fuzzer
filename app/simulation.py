from __future__ import annotations

import hashlib
import heapq
import json
import random
import time
from dataclasses import asdict, dataclass

from app.agents.behaviors import AgentContext, ExecutionAgent, MarketMaker, build_agents
from app.exchange import Account, CancelRequest, Exchange, Order
from app.schemas import WorldSpec


@dataclass
class SimulationResult:
    spec_hash: str
    result_hash: str
    seed: int
    timeline: list[dict]
    orders: list[dict]
    trades: list[dict]
    events: list[dict]
    agent_states: list[dict]
    summary: dict
    runtime_ms: float

    def to_dict(self) -> dict:
        return asdict(self)


def _latency_steps(latency_ms: int, profile: str) -> int:
    multiplier = {"low": 0.25, "normal": 1.0, "high": 4.0}[profile]
    return min(6, int(latency_ms * multiplier // 20))


def run_simulation(spec: WorldSpec) -> SimulationResult:
    started = time.perf_counter()
    rng = random.Random(spec.seed)
    exchange = Exchange([asset.ticker for asset in spec.assets], spec.exchange)
    agents = build_agents(spec.agents.populations, spec)
    agent_map = {agent.agent_id: agent for agent in agents}
    for agent in agents:
        exchange.register(
            Account(agent.agent_id, agent.capital_cents, {asset.ticker: 0 for asset in spec.assets})
        )

    fundamentals = {asset.ticker: asset.initial_fundamental_value_ticks for asset in spec.assets}
    prices = {asset.ticker: [asset.initial_price_ticks] for asset in spec.assets}
    liquidity_multiplier = 1.0
    pending: list[tuple[int, int, Order]] = []
    pending_sequence = 0
    events_log: list[dict] = []
    timeline: list[dict] = []
    agent_states: list[dict] = []
    trade_cursor = 0
    event_map: dict[int, list] = {}
    for event in spec.events:
        event_map.setdefault(event.simulation_step, []).append(event)

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
                    exchange.submit(order, 0)
                    agent.resting_order_ids.add(order.order_id)

    arrival_price = prices[spec.experiment.target_asset][-1]
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
            events_log.append({**event.model_dump(mode="json"), "step": step})

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
            _, _, order = heapq.heappop(pending)
            try:
                trades = exchange.submit(order, step)
                agent = agent_map[order.agent_id]
                if isinstance(agent, ExecutionAgent):
                    agent.executed_quantity += sum(trade.quantity for trade in trades)
                if order.remaining and order.order_type.value == "limit":
                    agent.resting_order_ids.add(order.order_id)
            except RuntimeError:
                # A delayed order can arrive during a configured exchange halt.
                continue

        observed_volume = sum(row["quantity"] for row in exchange.trade_log[trade_cursor:])
        for agent in agents:
            # Market makers replace only their own live quotes; cancel remains legal during halts.
            if isinstance(agent, MarketMaker):
                for symbol in list(exchange.books):
                    for order_id in list(agent.resting_order_ids):
                        if order_id in exchange.books[symbol].orders:
                            try:
                                exchange.cancel(CancelRequest(order_id, agent.agent_id, step), symbol)
                            except (KeyError, PermissionError):
                                # The order may already have filled or been canceled.
                                agent.resting_order_ids.discard(order_id)
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
                inventory = exchange.accounts[agent.agent_id].inventory.get(asset.ticker, 0)
                for order in agent.decide(context, rng, inventory):
                    pending_sequence += 1
                    arrival = step + _latency_steps(agent.latency_ms, spec.exchange.latency_profile)
                    if arrival <= step:
                        try:
                            trades = exchange.submit(order, step)
                            if isinstance(agent, ExecutionAgent):
                                agent.executed_quantity += sum(trade.quantity for trade in trades)
                            if order.remaining and order.order_type.value == "limit":
                                agent.resting_order_ids.add(order.order_id)
                        except RuntimeError:
                            # Agent orders are rejected while a halt is active.
                            continue
                    else:
                        heapq.heappush(pending, (arrival, pending_sequence, order))

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
        timeline.append(
            {
                "step": step,
                "asset_states": asset_states,
                "liquidity_multiplier": liquidity_multiplier,
                "events": [row for row in events_log if row["step"] == step],
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

    execution = next(agent for agent in agents if isinstance(agent, ExecutionAgent))
    strategy_trades = [
        trade
        for trade in exchange.trade_log
        if trade["buyer_id"] == execution.agent_id or trade["seller_id"] == execution.agent_id
    ]
    executed = sum(trade["quantity"] for trade in strategy_trades)
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
        "market_disruption": max(0.0, temporary - shortfall),
        "fees_cents": exchange.fee_account_cents,
    }
    deterministic = {
        "spec_hash": spec.specification_hash(),
        "timeline": timeline,
        "orders": exchange.order_log,
        "trades": exchange.trade_log,
        "events": events_log,
        "summary": summary,
    }
    result_hash = hashlib.sha256(
        json.dumps(deterministic, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return SimulationResult(
        spec.specification_hash(),
        result_hash,
        spec.seed,
        timeline,
        exchange.order_log,
        exchange.trade_log,
        events_log,
        agent_states,
        summary,
        (time.perf_counter() - started) * 1_000,
    )
