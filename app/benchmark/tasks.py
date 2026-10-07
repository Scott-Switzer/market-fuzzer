"""Benchmark task specifications and deterministic scoring (M10.5).

Every score is a bounded [0, 100] number derived only from the recorded session
result, so the same world + agent behaviour always yields the same score. Task
metrics are reported alongside the score so a leaderboard entry is auditable.
"""

from __future__ import annotations

from app.benchmark.model import SessionResult, TaskKind, TaskOutcome, TaskSpec
from app.benchmark.universe import BenchmarkUniverse

__all__ = [
    "build_task_spec",
    "evaluate",
    "max_drawdown_cents",
]


def build_task_spec(
    kind: TaskKind,
    universe: BenchmarkUniverse,
    *,
    focus_symbol: str | None = None,
    target_quantity: int = 50_000,
    max_order_quantity: int = 20_000,
    market_making_position: int = 2_000_000,
) -> TaskSpec:
    """Build the concrete instruction for one world."""

    symbols = universe.symbols
    if kind is TaskKind.EXECUTION:
        focus = focus_symbol or symbols[0]
        return TaskSpec(
            kind=kind,
            focus_symbol=focus,
            side="buy",
            target_quantity=target_quantity,
            agent_instruments=(focus,),
            initial_position_per_instrument=0,
            max_order_quantity=max_order_quantity,
        )
    if kind is TaskKind.MARKET_MAKING:
        focus = focus_symbol or symbols[min(1, len(symbols) - 1)]
        return TaskSpec(
            kind=kind,
            focus_symbol=focus,
            side="buy",
            target_quantity=0,
            agent_instruments=(focus,),
            initial_position_per_instrument=market_making_position,
            max_order_quantity=max_order_quantity,
        )
    return TaskSpec(
        kind=TaskKind.PORTFOLIO,
        focus_symbol=symbols[0],
        side="buy",
        target_quantity=0,
        agent_instruments=symbols,
        initial_position_per_instrument=0,
        max_order_quantity=max_order_quantity,
    )


def max_drawdown_cents(equity_curve: tuple[int, ...]) -> int:
    """Largest peak-to-trough decline of a cents equity curve (non-negative)."""

    peak = 0
    drawdown = 0
    for value in equity_curve:
        peak = max(peak, value)
        drawdown = max(drawdown, peak - value)
    return drawdown


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _sign(side: str) -> float:
    return 1.0 if side == "buy" else -1.0


def _execution(spec: TaskSpec, result: SessionResult) -> TaskOutcome:
    focus = spec.focus_symbol
    fills = tuple(
        fill for fill in result.agent_fills if fill.instrument_id == focus and fill.side == spec.side
    )
    filled = sum(fill.quantity for fill in fills)
    notional = sum(fill.quantity * fill.price_ticks for fill in fills)
    arrival = max(1, result.arrival_price_ticks)
    sign = _sign(spec.side)
    average_price = notional / filled if filled else 0.0
    shortfall_bps = sign * (average_price - arrival) / arrival * 10_000.0 if filled else 0.0
    completion = min(1.0, filled / spec.target_quantity) if spec.target_quantity else 0.0
    impact_bps = sign * (result.final_price_ticks - arrival) / arrival * 10_000.0
    shortfall_score = _clamp01(1.0 - max(0.0, shortfall_bps) / 50.0) if filled else 0.0
    score = 100.0 * (0.6 * completion + 0.4 * shortfall_score)
    score = max(0.0, score - 25.0 * len(result.violations))
    return TaskOutcome(
        kind=spec.kind.value,
        score=score,
        metrics={
            "completion": round(completion, 6),
            "filled_quantity": filled,
            "target_quantity": spec.target_quantity,
            "average_price_ticks": round(average_price, 6),
            "arrival_price_ticks": arrival,
            "implementation_shortfall_bps": round(shortfall_bps, 6),
            "market_impact_bps": round(impact_bps, 6),
            "shortfall_score": round(shortfall_score, 6),
            "fill_count": len(fills),
            "max_inventory_quantity": abs(result.agent_positions.get(focus, 0)),
        },
        violations=result.violations,
    )


def _market_making(spec: TaskSpec, result: SessionResult) -> TaskOutcome:
    pnl_cents = result.agent_final_value_cents - result.agent_initial_value_cents
    initial = max(1, result.agent_initial_value_cents)
    pnl_bps = pnl_cents / initial * 10_000.0
    fill_count = result.agent_maker_fill_count + result.agent_taker_fill_count
    maker_share = result.agent_maker_fill_count / fill_count if fill_count else 0.0
    max_inventory = max((abs(value) for value in result.agent_positions.values()), default=0)
    drawdown = max_drawdown_cents(result.equity_curve_cents)
    pnl_score = _clamp01(0.5 + pnl_bps / 200.0)
    score = 100.0 * (0.5 * pnl_score + 0.3 * result.quote_uptime + 0.2 * maker_share)
    score = max(0.0, score - 25.0 * len(result.violations))
    return TaskOutcome(
        kind=spec.kind.value,
        score=score,
        metrics={
            "pnl_cents": int(pnl_cents),
            "pnl_bps": round(pnl_bps, 6),
            "quote_uptime": round(result.quote_uptime, 6),
            "maker_share": round(maker_share, 6),
            "maker_fill_count": result.agent_maker_fill_count,
            "taker_fill_count": result.agent_taker_fill_count,
            "max_inventory_quantity": max_inventory,
            "max_drawdown_cents": drawdown,
        },
        violations=result.violations,
    )


def _portfolio(spec: TaskSpec, result: SessionResult) -> TaskOutcome:
    initial = max(1, result.agent_initial_value_cents)
    total_return = (result.agent_final_value_cents - initial) / initial
    drawdown = max_drawdown_cents(result.equity_curve_cents) / initial
    turnover = sum(fill.quantity * fill.price_ticks for fill in result.agent_fills) / initial
    return_score = _clamp01(0.5 + total_return / 0.10)
    drawdown_score = _clamp01(1.0 - drawdown / 0.10)
    score = 100.0 * (0.6 * return_score + 0.4 * drawdown_score)
    score = max(0.0, score - 25.0 * len(result.violations))
    return TaskOutcome(
        kind=spec.kind.value,
        score=score,
        metrics={
            "total_return": round(total_return, 6),
            "max_drawdown_fraction": round(drawdown, 6),
            "turnover_fraction": round(turnover, 6),
            "final_value_cents": result.agent_final_value_cents,
            "initial_value_cents": result.agent_initial_value_cents,
            "instrument_count": len(result.instruments),
        },
        violations=result.violations,
    )


def evaluate(spec: TaskSpec, result: SessionResult) -> TaskOutcome:
    """Score one world's session result under its task specification."""

    if spec.kind is TaskKind.EXECUTION:
        return _execution(spec, result)
    if spec.kind is TaskKind.MARKET_MAKING:
        return _market_making(spec, result)
    return _portfolio(spec, result)
