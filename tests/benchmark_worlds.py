"""Shared world-construction helpers for the benchmark test suite (M10.6.1).

A universe is now built from a resolved plan world plus a process registry, so a
test that only cares about *a* world needs a short, uniform way to state one.
These helpers keep that boilerplate in a single place. The benchmark's own
acceptance tests -- the registry, the plan, and sealed evaluation -- build their
worlds explicitly instead, because there the provenance is the thing under test.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.benchmark.plan import (
    DatasetSplit,
    EvaluationPlan,
    EvaluationWorldTemplate,
    PlannedWorld,
    resolve_world,
)
from app.benchmark.process_registry import (
    FAMILIAR_FAMILY_ID,
    MarketProcessRegistry,
    default_process_registry,
)
from app.benchmark.universe import (
    FAMILIAR_ECOLOGY,
    BenchmarkUniverse,
    EcologyProfile,
    EvaluationPartition,
    build_universe,
)
from app.market.calendar import trading_days

__all__ = [
    "TEST_PLAN_ID",
    "TEST_PLAN_VERSION",
    "benchmark_universe",
    "day_sessions",
    "planned_world",
    "test_plan",
]

TEST_PLAN_ID = "test_worlds_v1"
TEST_PLAN_VERSION = "v1"


def day_sessions(days: int = 3, *, start: date = date(2026, 6, 1)) -> tuple[date, ...]:
    """``days`` trading sessions starting at ``start``."""

    return tuple(trading_days(start, start + timedelta(days=2 * days + 7))[:days])


def test_plan(*templates: EvaluationWorldTemplate) -> EvaluationPlan:
    """A test plan over the given templates."""

    return EvaluationPlan(plan_id=TEST_PLAN_ID, version=TEST_PLAN_VERSION, worlds=tuple(templates))


def planned_world(
    *,
    index: int = 0,
    world_id: str = "w-test",
    seed: int = 7,
    partition: EvaluationPartition = EvaluationPartition.FAMILIAR,
    family_id: str = FAMILIAR_FAMILY_ID,
    ecology: EcologyProfile = FAMILIAR_ECOLOGY,
    registry: MarketProcessRegistry | None = None,
    plan: EvaluationPlan | None = None,
) -> PlannedWorld:
    """Resolve one concrete world from explicit values."""

    resolved_registry = registry if registry is not None else default_process_registry()
    split = DatasetSplit.TRAINABLE if partition is EvaluationPartition.TRAINING else DatasetSplit.PUBLIC_EVAL
    resolved_plan = (
        plan
        if plan is not None
        else test_plan(
            EvaluationWorldTemplate(
                family_id=str(family_id),
                ecology_id=ecology.label,
                split=split,
                partition=partition,
            )
        )
    )
    return resolve_world(
        plan=resolved_plan,
        index=index,
        registry=resolved_registry,
        base_seed=seed,
        seed=seed,
        world_identifier=world_id,
    )


def benchmark_universe(
    *,
    universe_id: str,
    world_id: str,
    seed: int,
    ecology: EcologyProfile = FAMILIAR_ECOLOGY,
    family_id: str = FAMILIAR_FAMILY_ID,
    partition: EvaluationPartition = EvaluationPartition.FAMILIAR,
    security_count: int = 8,
    sessions: tuple[date, ...] | None = None,
    registry: MarketProcessRegistry | None = None,
) -> BenchmarkUniverse:
    """Build a universe for one explicit world."""

    resolved_registry = registry if registry is not None else default_process_registry()
    world = planned_world(
        world_id=world_id,
        seed=seed,
        partition=partition,
        family_id=family_id,
        ecology=ecology,
        registry=resolved_registry,
    )
    return build_universe(
        universe_id=universe_id,
        planned=world,
        registry=resolved_registry,
        security_count=security_count,
        sessions=sessions if sessions is not None else day_sessions(3),
    )
