"""Benchmark runner: sealed worlds, public/hidden partitions, and the report.

``run_benchmark`` generates ``worlds`` sealed synthetic worlds from the same
generator family. Half of them are *public* (baseline distribution) and half are
*hidden* (distribution/parameter-ecology holdout: different volatility, depth,
fee schedule, and agent mix behind the same interface). The *distribution
generalization gap* is the difference in mean score between the two partitions.

M10.5 is a distribution holdout, not a process-family holdout: both partitions
still use the same GJR-GARCH-t generator family. True generator-family OOD
evaluation is M10.6 scope.

Evaluation validity: a world where the external agent could not be reached
(``agent_unavailable``) or violated the response protocol (``agent_protocol``) is
not a model decision and is excluded from the official public/hidden means. If
any required evaluation world is invalid, the whole run is non-scoreable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.benchmark.hashing import digest_many
from app.benchmark.model import (
    EVALUATION_VALID,
    INVALID_AGENT_PROTOCOL,
    INVALID_AGENT_UNAVAILABLE,
    INVALID_INTERNAL,
    TaskKind,
    TaskOutcome,
)
from app.benchmark.port import (
    AGENT_PROTOCOL,
    AGENT_UNAVAILABLE,
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
    scoreable: bool
    agent_failure: str | None


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    """The aggregate benchmark report over all evaluation worlds."""

    agent_name: str
    task: str
    evaluation_worlds: int
    valid_worlds: int
    securities_encountered: int
    exchange_events: int
    outcomes: tuple[WorldOutcome, ...]
    public_score: float
    hidden_score: float
    valid_public_worlds: int
    valid_hidden_worlds: int
    generalization_gap: float
    generalization_scope: str
    weakest_environment: str
    replay_package: dict[str, Any]
    scoreable: bool
    run_status: str
    invalid_worlds: tuple[str, ...]

    def render(self) -> str:
        """Render the human-readable benchmark report."""

        lines = [
            "FINANCIAL WORLD FACTORY BENCHMARK",
            "",
            f"Agent: {self.agent_name}",
            f"Task: {_TASK_TITLES.get(TaskKind(self.task), self.task)}",
            f"Evaluation worlds: {self.evaluation_worlds} sealed synthetic worlds",
            f"Valid worlds: {self.valid_worlds}",
            f"Securities encountered: {self.securities_encountered}",
            f"Exchange events: {self.exchange_events:,}",
            "",
        ]
        lines.extend(_headline_lines(self.task, self.outcomes))
        # A partition with no *valid* worlds must never render as a numeric 0.0 —
        # that would read as a real (and terrible) score for a run in which the
        # agent simply failed to respond.
        public_line = (
            f"Public worlds score      {self.public_score:6.1f}  ({self.valid_public_worlds} valid worlds)"
            if self.valid_public_worlds
            else "Public worlds score      n/a (no valid worlds)"
        )
        hidden_line = (
            f"Hidden worlds score      {self.hidden_score:6.1f}  ({self.valid_hidden_worlds} valid worlds)"
            if self.valid_hidden_worlds
            else "Hidden worlds score      n/a (no valid worlds)"
        )
        gap_line = (
            f"Distribution generalization gap {self.generalization_gap:+6.1f}"
            if self.valid_public_worlds and self.valid_hidden_worlds
            else "Distribution generalization gap n/a (both partitions required)"
        )
        lines.extend(
            [
                "",
                public_line,
                hidden_line,
                gap_line,
                "",
                "Weakest environment:",
                self.weakest_environment,
                "",
                f"Replay package: {len(self.outcomes)} worlds, "
                f"digest {self.replay_package['replay_digest'][:32]}",
                "",
                f"Validity: {self.run_status}",
            ]
        )
        if not self.scoreable:
            lines.append(
                f"Invalid worlds: {len(self.invalid_worlds)} of {self.evaluation_worlds} "
                f"({', '.join(self.invalid_worlds[:4])}"
                f"{', ...' if len(self.invalid_worlds) > 4 else ''})"
            )
            lines.append("Official benchmark score: WITHHELD (invalid evaluation)")
        lines.extend(
            [
                "",
                "  None of these price paths, securities, order books, or events existed "
                "before this benchmark generated them.",
            ]
        )
        return "\n".join(lines)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _headline_lines(task: str, outcomes: tuple[WorldOutcome, ...]) -> list[str]:
    scored = [item for item in outcomes if item.scoreable]
    if not scored:
        # No valid worlds: the agent never produced a scoreable decision, so the
        # task metrics are not a result and must not be headlined as one.
        return ["Task metrics             withheld (no valid worlds)"]
    if task == TaskKind.EXECUTION.value:
        completions = [float(item.metrics["completion"]) for item in scored]
        shortfalls = [float(item.metrics["implementation_shortfall_bps"]) for item in scored]
        inventory = [int(item.metrics["max_inventory_quantity"]) for item in scored]
        violations = sum(len(item.violations) for item in outcomes)
        return [
            f"Completion               {_mean(completions) * 100:6.1f}%",
            f"Implementation shortfall  {_mean(shortfalls):6.1f} bps",
            f"Tail shortfall           {max(shortfalls) if shortfalls else 0.0:6.1f} bps",
            f"Peak inventory exposure  {max(inventory) if inventory else 0:6d}",
            f"Constraint violations    {violations:6d}",
        ]
    if task == TaskKind.MARKET_MAKING.value:
        pnl_bps = [float(item.metrics["pnl_bps"]) for item in scored]
        uptime = [float(item.metrics["quote_uptime"]) for item in scored]
        drawdown = [int(item.metrics["max_drawdown_cents"]) for item in scored]
        violations = sum(len(item.violations) for item in outcomes)
        return [
            f"P&L                       {_mean(pnl_bps):6.1f} bps",
            f"Quote uptime             {_mean(uptime) * 100:6.1f}%",
            f"Worst drawdown           {max(drawdown) if drawdown else 0:6d} cents",
            f"Constraint violations    {violations:6d}",
        ]
    total_return = [float(item.metrics["total_return"]) for item in scored]
    drawdown_fraction = [float(item.metrics["max_drawdown_fraction"]) for item in scored]
    turnover = [float(item.metrics["turnover_fraction"]) for item in scored]
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


def _run_status(invalid: list[WorldOutcome]) -> str:
    failures = {item.agent_failure for item in invalid}
    if AGENT_UNAVAILABLE in failures:
        return INVALID_AGENT_UNAVAILABLE
    if AGENT_PROTOCOL in failures:
        return INVALID_AGENT_PROTOCOL
    return INVALID_INTERNAL


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
            "scoreable": result.scoreable,
            "agent_failure": result.agent_failure,
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
                scoreable=result.scoreable,
                agent_failure=result.agent_failure,
            )
        )

    invalid = [item for item in outcomes if not item.scoreable]
    valid = [item for item in outcomes if item.scoreable]
    public = [item.score for item in valid if item.holdout == "public"]
    hidden = [item.score for item in valid if item.holdout == "hidden"]
    scoreable = not invalid and bool(valid)
    weakest = min(valid, key=lambda item: item.score) if valid else None
    replay_package = {
        "task": kind.value,
        "agent": agent_name,
        "worlds": replay_worlds,
        "replay_digest": digest_many([item.replay for item in outcomes]),
        "scoreable": scoreable,
        "run_status": EVALUATION_VALID if scoreable else _run_status(invalid),
    }
    return BenchmarkReport(
        agent_name=agent_name,
        task=kind.value,
        evaluation_worlds=worlds,
        valid_worlds=len(valid),
        securities_encountered=len(symbols_seen),
        exchange_events=total_events,
        outcomes=tuple(outcomes),
        public_score=_mean(public),
        hidden_score=_mean(hidden),
        valid_public_worlds=len(public),
        valid_hidden_worlds=len(hidden),
        generalization_gap=_mean(hidden) - _mean(public),
        generalization_scope="distribution",
        weakest_environment=(
            f"{weakest.holdout} / {weakest.profile_label} (world {weakest.world_id})"
            if weakest is not None
            else "withheld (no valid worlds)"
        ),
        replay_package=replay_package,
        scoreable=scoreable,
        run_status=EVALUATION_VALID if scoreable else _run_status(invalid),
        invalid_worlds=tuple(item.world_id for item in invalid),
    )
