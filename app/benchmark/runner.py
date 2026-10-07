"""Benchmark runner: sealed worlds, three partitions, and the report.

``run_benchmark`` generates ``worlds`` sealed synthetic worlds and assigns each
one an evaluation partition:

* **familiar** -- baseline ecology, the familiar process family
  (``gjr_factor_t_v1``). This is what the agent is expected to have adapted to.
* **distribution** -- shifted ecology (volatility, depth, fees, and background
  agent mix) but the *same* process family. This is the M10.5
  distribution/parameter-ecology holdout.
* **mechanism** -- shifted ecology *and* a process family the agent never saw
  (``stochastic_vol_factor_t_v1`` or ``markov_regime_jump_factor_v1``).

Three gaps are reported, each isolating one axis:

* **distribution generalization gap** -- ``mean(distribution) - mean(familiar)``:
the ecology/parameter shift at a fixed process family.
* **mechanism generalization gap** -- ``mean(mechanism) - mean(familiar)``: the
joint shift (ecology *and* process family) relative to what the agent saw.
* **process-family generalization gap** -- ``mean(mechanism) - mean(distribution)``:
the *isolated* market-process effect, because the mechanism and distribution
partitions share the same shifted ecology and differ only in the process family.
This is the true market-process generalization gap.

A large negative process-family gap is the signature of an agent that learned the
familiar generator (its volatility clustering, tails, and drift dynamics) rather
than the execution task.

Evaluation validity: a world where the external agent could not be reached
(``agent_unavailable``) or violated the response protocol (``agent_protocol``) is
not a model decision and is excluded from the official partition means. If any
required evaluation world is invalid, the whole run is non-scoreable.
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
    DISTRIBUTION_ECOLOGY,
    FAMILIAR_ECOLOGY,
    BenchmarkUniverse,
    EcologyProfile,
    EvaluationPartition,
    build_universe,
)
from app.market.calendar import trading_days
from app.market.process import FAMILIAR_FAMILY, MECHANISM_FAMILIES, ProcessFamilyKind

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
    partition: str
    process_family: str
    ecology_label: str
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
    familiar_score: float
    distribution_score: float
    mechanism_score: float
    valid_familiar_worlds: int
    valid_distribution_worlds: int
    valid_mechanism_worlds: int
    distribution_gap: float
    mechanism_gap: float
    process_family_gap: float
    mechanism_families: tuple[str, ...]
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
        lines.extend(
            [
                "",
                _partition_line("Familiar", self.familiar_score, self.valid_familiar_worlds),
                _partition_line("Distribution", self.distribution_score, self.valid_distribution_worlds),
                _partition_line("Mechanism", self.mechanism_score, self.valid_mechanism_worlds),
                "",
                _gap_line(
                    "Distribution generalization gap",
                    self.distribution_gap,
                    self.valid_familiar_worlds,
                    self.valid_distribution_worlds,
                ),
                _gap_line(
                    "Mechanism generalization gap",
                    self.mechanism_gap,
                    self.valid_familiar_worlds,
                    self.valid_mechanism_worlds,
                ),
                _gap_line(
                    "Process-family generalization gap",
                    self.process_family_gap,
                    self.valid_distribution_worlds,
                    self.valid_mechanism_worlds,
                ),
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


def _partition_line(name: str, score: float, valid_worlds: int) -> str:
    # A partition with no *valid* worlds must never render as a numeric 0.0 —
    # that would read as a real (and terrible) score for a run in which the
    # agent simply failed to respond.
    label = f"{name} worlds score"
    if valid_worlds:
        return f"{label:<26}{score:6.1f}  ({valid_worlds} valid worlds)"
    return f"{label:<26}n/a (no valid worlds)"


def _gap_line(name: str, gap: float, baseline_worlds: int, compared_worlds: int) -> str:
    if baseline_worlds and compared_worlds:
        return f"{name:<36}{gap:+6.1f}"
    return f"{name:<36}n/a (both partitions required)"


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


def world_design(index: int) -> tuple[EvaluationPartition, ProcessFamilyKind, EcologyProfile]:
    """Assign world ``index`` to a partition, a process family, and an ecology.

    The assignment is evaluator-private: it is never surfaced through the
    agent-facing observation protocol. Mechanism worlds rotate through the
    held-out families so a single mechanism score averages over more than one
    unseen generator.
    """

    bucket = index % 3
    if bucket == 0:
        return EvaluationPartition.FAMILIAR, FAMILIAR_FAMILY, FAMILIAR_ECOLOGY
    if bucket == 1:
        return EvaluationPartition.DISTRIBUTION, FAMILIAR_FAMILY, DISTRIBUTION_ECOLOGY
    family = MECHANISM_FAMILIES[(index // 3) % len(MECHANISM_FAMILIES)]
    return EvaluationPartition.MECHANISM, family, DISTRIBUTION_ECOLOGY


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
        partition, family, ecology = world_design(index)
        seed = _world_seed(base_seed, index)
        world_id = f"bench-{index:04d}-{partition.value}"
        universe: BenchmarkUniverse = build_universe(
            universe_id=f"synth-exchange-{kind.value}-{index:04d}",
            world_id=world_id,
            seed=seed,
            ecology=ecology,
            family=family,
            partition=partition,
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
                ecology=ecology,
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
            "partition": partition.value,
            "process_family": family.value,
            "ecology": ecology.label,
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
                partition=partition.value,
                process_family=family.value,
                ecology_label=ecology.label,
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
    familiar = _scores(valid, EvaluationPartition.FAMILIAR)
    distribution = _scores(valid, EvaluationPartition.DISTRIBUTION)
    mechanism = _scores(valid, EvaluationPartition.MECHANISM)
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
        familiar_score=_mean(familiar),
        distribution_score=_mean(distribution),
        mechanism_score=_mean(mechanism),
        valid_familiar_worlds=len(familiar),
        valid_distribution_worlds=len(distribution),
        valid_mechanism_worlds=len(mechanism),
        distribution_gap=_mean(distribution) - _mean(familiar),
        mechanism_gap=_mean(mechanism) - _mean(familiar),
        process_family_gap=_mean(mechanism) - _mean(distribution),
        mechanism_families=tuple(
            sorted({item.process_family for item in valid if item.partition == EvaluationPartition.MECHANISM})
        ),
        generalization_scope="process-family",
        weakest_environment=(
            f"{weakest.partition} / {weakest.process_family} / {weakest.ecology_label} "
            f"(world {weakest.world_id})"
            if weakest is not None
            else "withheld (no valid worlds)"
        ),
        replay_package=replay_package,
        scoreable=scoreable,
        run_status=EVALUATION_VALID if scoreable else _run_status(invalid),
        invalid_worlds=tuple(item.world_id for item in invalid),
    )


def _scores(outcomes: list[WorldOutcome], partition: EvaluationPartition) -> list[float]:
    return [item.score for item in outcomes if item.partition == partition]
