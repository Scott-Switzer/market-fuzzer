"""Benchmark runner: sealed worlds, public/hidden partitions, and the report.

``run_benchmark`` generates ``worlds`` sealed synthetic worlds from the same
generator family. Half of them are *public* (baseline family) and half are
*hidden* (mechanism-holdout family). The generalization gap is the difference in
mean score between the two partitions: a positive gap means the agent
generalizes to the held-out mechanisms; a large negative gap means it has
overfit the public generator.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.benchmark.hashing import digest_many
from app.benchmark.model import TaskKind, TaskOutcome
from app.benchmark.port import (
    StrategyDecisionPort,
    accumulate_port,
    passive_maker_port,
    twap_port,
)
from app.benchmark.session import BenchmarkSession, SessionConfig
from app.benchmark.tasks import build_task_spec, evaluate
from app.benchmark.universe import (
    HIDDEN_PROFILE,
    PUBLIC_PROFILE,
    BenchmarkUniverse,
    build_universe,
)
from app.market.calendar import trading_days

__all__ = ["BenchmarkReport", "WorldOutcome", "builtin_port_factory", "run_benchmark"]

_TASK_TITLES = {
    TaskKind.EXECUTION: "Optimal Execution",
    TaskKind.MARKET_MAKING: "Market Making",
    TaskKind.PORTFOLIO: "Portfolio / Trading Agent",
}

# Agent starting capital in cents, sized so the task metric is meaningful.
_AGENT_CASH_CENTS = {
    TaskKind.EXECUTION: 10**12,
    TaskKind.MARKET_MAKING: 10**13,
    TaskKind.PORTFOLIO: 10**9,
}


@dataclass(frozen=True, slots=True)
class WorldOutcome:
    """One sealed world's scored result plus its replay provenance."""

    world_id: str
    seed: int
    holdout: str
    profile_label: str
    score: float
    metrics: dict[str, float | int]
    violations: tuple[str, ...]
    replay: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    """The aggregate benchmark report over all evaluation worlds."""

    agent_name: str
    task: str
    evaluation_worlds: int
    securities_encountered: int
    exchange_events: int
    outcomes: tuple[WorldOutcome, ...]
    public_score: float
    hidden_score: float
    generalization_gap: float
    weakest_environment: str
    replay_package: dict[str, Any]

    def render(self) -> str:
        """Render the human-readable benchmark report."""

        lines = [
            "FINANCIAL WORLD FACTORY BENCHMARK",
            "",
            f"Agent: {self.agent_name}",
            f"Task: {_TASK_TITLES.get(TaskKind(self.task), self.task)}",
            f"Evaluation worlds: {self.evaluation_worlds} sealed synthetic worlds",
            f"Securities encountered: {self.securities_encountered}",
            f"Exchange events: {self.exchange_events:,}",
            "",
        ]
        lines.extend(_headline_lines(self.task, self.outcomes))
        lines.extend(
            [
                "",
                f"Public worlds score      {self.public_score:6.1f}",
                f"Hidden worlds score      {self.hidden_score:6.1f}",
                f"Generalization gap       {self.generalization_gap:+6.1f}",
                "",
                "Weakest environment:",
                self.weakest_environment,
                "",
                f"Replay package: {len(self.outcomes)} worlds, "
                f"digest {self.replay_package['replay_digest'][:32]}",
                "",
                "  None of these price paths, securities, order books, or events existed "
                "before this benchmark generated them.",
            ]
        )
        return "\n".join(lines)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _headline_lines(task: str, outcomes: tuple[WorldOutcome, ...]) -> list[str]:
    if task == TaskKind.EXECUTION.value:
        completions = [float(item.metrics["completion"]) for item in outcomes]
        shortfalls = [float(item.metrics["implementation_shortfall_bps"]) for item in outcomes]
        inventory = [int(item.metrics["max_inventory_quantity"]) for item in outcomes]
        violations = sum(len(item.violations) for item in outcomes)
        return [
            f"Completion               {_mean(completions) * 100:6.1f}%",
            f"Implementation shortfall  {_mean(shortfalls):6.1f} bps",
            f"Tail shortfall           {max(shortfalls) if shortfalls else 0.0:6.1f} bps",
            f"Max inventory exposure   {max(inventory) if inventory else 0:6d}",
            f"Constraint violations    {violations:6d}",
        ]
    if task == TaskKind.MARKET_MAKING.value:
        pnl_bps = [float(item.metrics["pnl_bps"]) for item in outcomes]
        uptime = [float(item.metrics["quote_uptime"]) for item in outcomes]
        drawdown = [int(item.metrics["max_drawdown_cents"]) for item in outcomes]
        violations = sum(len(item.violations) for item in outcomes)
        return [
            f"P&L                       {_mean(pnl_bps):6.1f} bps",
            f"Quote uptime             {_mean(uptime) * 100:6.1f}%",
            f"Worst drawdown           {max(drawdown) if drawdown else 0:6d} cents",
            f"Constraint violations    {violations:6d}",
        ]
    total_return = [float(item.metrics["total_return"]) for item in outcomes]
    drawdown_fraction = [float(item.metrics["max_drawdown_fraction"]) for item in outcomes]
    turnover = [float(item.metrics["turnover_fraction"]) for item in outcomes]
    violations = sum(len(item.violations) for item in outcomes)
    return [
        f"Mean total return        {_mean(total_return) * 100:6.2f}%",
        f"Worst drawdown           {max(drawdown_fraction) * 100 if drawdown_fraction else 0.0:6.2f}%",
        f"Turnover                 {_mean(turnover) * 100:6.2f}%",
        f"Constraint violations    {violations:6d}",
    ]


def _world_seed(base_seed: int, index: int) -> int:
    return (base_seed * 1_000_003 + index * 7_919) % 2_147_483_647


def _calendar(start: date, days: int) -> tuple[date, ...]:
    return tuple(trading_days(start, start + timedelta(days=2 * days + 7))[:days])


def builtin_port_factory(
    kind: TaskKind, *, slice_quantity: int = 2_500
) -> Callable[[int], StrategyDecisionPort]:
    """A factory returning deterministic built-in baseline ports per task."""

    if kind is TaskKind.EXECUTION:
        return lambda _: twap_port(slice_quantity=slice_quantity)
    if kind is TaskKind.MARKET_MAKING:
        return lambda _: passive_maker_port(spread_ticks=3, quantity=200)
    return lambda _: accumulate_port(slice_quantity=1_000, max_shares_per_instrument=20_000)


def run_benchmark(
    *,
    kind: TaskKind,
    port_factory: Callable[[int], StrategyDecisionPort],
    worlds: int = 32,
    security_count: int = 8,
    days: int = 5,
    steps_per_day: int = 30,
    base_seed: int = 20_261_006,
    target_quantity: int = 50_000,
    max_order_quantity: int = 20_000,
    start_date: date = date(2026, 6, 1),
    session_config: SessionConfig | None = None,
) -> BenchmarkReport:
    """Generate sealed worlds, run the agent, and score it across all worlds."""

    if worlds < 1:
        raise ValueError("worlds must be at least one")
    if session_config is None:
        session_config = SessionConfig(steps_per_day=steps_per_day, agent_cash_cents=_AGENT_CASH_CENTS[kind])
    sessions = _calendar(start_date, days)
    outcomes: list[WorldOutcome] = []
    replay_worlds: list[dict[str, Any]] = []
    symbols_seen: set[str] = set()
    total_events = 0
    agent_name = "unnamed-agent"

    for index in range(worlds):
        holdout = "public" if index % 2 == 0 else "hidden"
        profile = PUBLIC_PROFILE if holdout == "public" else HIDDEN_PROFILE
        seed = _world_seed(base_seed, index)
        world_id = f"bench-{index:04d}-{holdout}"
        universe: BenchmarkUniverse = build_universe(
            universe_id=f"synth-exchange-{kind.value}-{index:04d}",
            world_id=world_id,
            seed=seed,
            profile=profile,
            security_count=security_count,
            sessions=sessions,
        )
        task_spec = build_task_spec(
            kind,
            universe,
            target_quantity=target_quantity,
            max_order_quantity=max_order_quantity,
        )
        port = port_factory(index)
        agent_name = port.name
        try:
            result = BenchmarkSession(
                universe=universe,
                profile=profile,
                task=task_spec,
                port=port,
                config=session_config,
            ).run()
        finally:
            port.close()
        outcome: TaskOutcome = evaluate(task_spec, result)
        symbols_seen.update(result.instruments)
        total_events += result.event_count
        replay = {
            "world_id": world_id,
            "universe_id": universe.universe_id,
            "seed": seed,
            "holdout": holdout,
            "profile": profile.label,
            "market_logical_sha256": result.market_logical_sha256,
            "ledger_digest": result.ledger_digest,
            "action_digest": result.action_digest,
            "event_count": result.event_count,
            "trade_count": result.trade_count,
            "steps_total": result.steps_total,
            "score": round(outcome.score, 6),
        }
        replay_worlds.append(replay)
        outcomes.append(
            WorldOutcome(
                world_id=world_id,
                seed=seed,
                holdout=holdout,
                profile_label=profile.label,
                score=outcome.score,
                metrics=dict(outcome.metrics),
                violations=outcome.violations,
                replay=replay,
            )
        )

    public = [item.score for item in outcomes if item.holdout == "public"]
    hidden = [item.score for item in outcomes if item.holdout == "hidden"]
    weakest = min(outcomes, key=lambda item: item.score)
    replay_package = {
        "task": kind.value,
        "agent": agent_name,
        "worlds": replay_worlds,
        "replay_digest": digest_many([item.replay for item in outcomes]),
    }
    return BenchmarkReport(
        agent_name=agent_name,
        task=kind.value,
        evaluation_worlds=worlds,
        securities_encountered=len(symbols_seen),
        exchange_events=total_events,
        outcomes=tuple(outcomes),
        public_score=_mean(public),
        hidden_score=_mean(hidden),
        generalization_gap=_mean(hidden) - _mean(public),
        weakest_environment=f"{weakest.holdout} / {weakest.profile_label} (world {weakest.world_id})",
        replay_package=replay_package,
    )
