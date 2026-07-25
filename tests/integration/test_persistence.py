"""Persistence + repository tests (reset brief section 11, section 17: migration + idempotency)."""

from __future__ import annotations

import pytest

from app.domain.run import FailureReason, Job, Run, RunStage
from app.domain.strategy_spec import StrategySpec, StrategyType
from app.domain.strategy_version import StrategyVersion
from app.evidence.artifact_store import ArtifactRef
from app.persistence.database import create_all, make_engine, make_session_factory, session_scope
from app.persistence.repositories import (
    JobRepository,
    ProjectRepository,
    RunRepository,
    StrategyRepository,
)


@pytest.fixture
def factory():
    engine = make_engine("sqlite:///:memory:")
    create_all(engine)
    return make_session_factory(engine)


def _spec() -> StrategySpec:
    return StrategySpec(
        name="Momentum",
        original_thesis="top momentum names monthly",
        strategy_type=StrategyType.CROSS_SECTIONAL_FACTOR,
        universe=["AAPL", "MSFT", "NVDA"],
        benchmark="SPY",
    )


def test_schema_creates_all_tables():
    engine = make_engine("sqlite:///:memory:")
    create_all(engine)
    from sqlalchemy import inspect

    names = set(inspect(engine).get_table_names())
    assert {
        "projects",
        "strategies",
        "strategy_versions",
        "runs",
        "jobs",
        "failures",
        "artifact_index",
        "evidence_verification",
        "data_sources",
    } <= names


def test_project_strategy_version_roundtrip(factory):
    spec = _spec()
    with session_scope(factory) as s:
        proj = ProjectRepository(s).create(name="My Project")
        pid = proj.id
        StrategyRepository(s).create(pid, spec)
        StrategyRepository(s).add_version(StrategyVersion.from_spec(spec))

    with session_scope(factory) as s:
        v = StrategyRepository(s).get_version(spec.strategy_id, 1)
        assert v is not None
        assert v.canonical_hash == spec.canonical_hash
        # spec survives the JSON roundtrip with identical hash
        assert v.spec.compute_hash() == spec.canonical_hash


def test_run_stage_persistence(factory):
    spec = _spec()
    with session_scope(factory) as s:
        proj = ProjectRepository(s).create(name="P")
        run = Run(
            project_id=proj.id,
            strategy_id=spec.strategy_id,
            strategy_version=1,
            strategy_hash=spec.canonical_hash,
            data_mode="synthetic_fixture",
        )
        rid = run.run_id
        rr = RunRepository(s)
        rr.create(run)
        rr.mark_stage(rid, RunStage.HISTORICAL_BACKTEST)
        rr.mark_stage(rid, RunStage.HISTORICAL_BACKTEST)  # idempotent

    with session_scope(factory) as s:
        row = RunRepository(s).get(rid)
        assert row is not None
        assert row.stages_completed == ["historical_backtest"]


def test_artifact_index_stores_no_filesystem_path(factory):
    spec = _spec()
    with session_scope(factory) as s:
        proj = ProjectRepository(s).create(name="P")
        run = Run(
            project_id=proj.id,
            strategy_id=spec.strategy_id,
            strategy_version=1,
            strategy_hash=spec.canonical_hash,
            data_mode="synthetic_fixture",
        )
        RunRepository(s).create(run)
        ref = ArtifactRef(store="filesystem", key="run/x/metrics.json", sha256="a" * 64, size=10)
        row = RunRepository(s).index_artifact(run.run_id, ref)
        assert row.artifact_key == "run/x/metrics.json"
        assert not row.artifact_key.startswith("/")  # no absolute path indexed


def test_job_idempotent_submit(factory):
    with session_scope(factory) as s:
        repo = JobRepository(s)
        j1 = Job(idempotency_key="run-abc:historical", stage=RunStage.HISTORICAL_BACKTEST)
        stored1 = repo.submit(j1)
        # second submit with SAME idempotency key returns the existing job
        j2 = Job(idempotency_key="run-abc:historical", stage=RunStage.HISTORICAL_BACKTEST)
        stored2 = repo.submit(j2)
        assert stored1.job_id == stored2.job_id  # no duplicate


def test_job_state_persistence(factory):
    with session_scope(factory) as s:
        repo = JobRepository(s)
        j = Job(idempotency_key="k", stage=RunStage.STRESS_CAMPAIGN)
        repo.submit(j)
        j.mark_running()
        j.mark_failed(FailureReason(code="data_unavailable", message="timeout", retryable=True))
        repo.save(j)
        jid = j.job_id

    with session_scope(factory) as s:
        loaded = JobRepository(s).get(jid)
        assert loaded is not None
        assert loaded.state.value == "failed"
        assert loaded.attempts == 1
        assert loaded.failure is not None
        assert loaded.failure.retryable is True
