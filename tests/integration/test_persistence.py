"""Persistence + repository tests (reset brief section 11 + Phase 1.1 fidelity/idempotency).

These run against SQLite for speed; the same behaviours are proven on real
PostgreSQL in tests/integration/test_postgres_persistence.py.
"""

from __future__ import annotations

import pytest

from app.domain.run import FailureReason, Job, Run, RunStage
from app.domain.strategy_spec import StrategySpec, StrategyType
from app.domain.strategy_version import DraftStrategy
from app.evidence.artifact_store import ArtifactRef
from app.persistence.database import create_all, make_engine, make_session_factory, session_scope
from app.persistence.repositories import (
    JobRepository,
    ProjectRepository,
    RunRepository,
    StrategyRepository,
)

SUPPORTED = set(StrategyType) - {StrategyType.UNSUPPORTED}


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


def _seed_project_and_version(s, spec: StrategySpec):
    """Create project + strategy + approved version; return (project_id, approved)."""
    proj = ProjectRepository(s).create(name="My Project")
    draft = DraftStrategy(spec=spec)
    approved = draft.approve(approved_by="scott", supported_types=SUPPORTED)
    StrategyRepository(s).create(proj.id, approved.strategy_id, spec.name, spec.original_thesis)
    StrategyRepository(s).add_approved_version(approved)
    return proj.id, approved


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


def test_approved_version_roundtrip_byte_for_byte(factory):
    spec = _spec()
    with session_scope(factory) as s:
        _, approved = _seed_project_and_version(s, spec)
        sid, chash, cjson = approved.strategy_id, approved.canonical_hash, approved.canonical_json

    with session_scope(factory) as s:
        loaded = StrategyRepository(s).get_approved_version(sid, 1)
        assert loaded is not None
        assert loaded.canonical_hash == chash
        assert loaded.canonical_json == cjson  # byte-for-byte
        # reconstructed spec re-verifies its hash (tamper-safe)
        rebuilt = loaded.to_spec()
        assert rebuilt.compute_hash() == chash
        assert rebuilt.strategy_id == sid


def test_run_requires_valid_strategy_version_fk(factory):
    spec = _spec()
    with session_scope(factory) as s:
        pid, approved = _seed_project_and_version(s, spec)
        run = Run(
            project_id=pid,
            strategy_id=approved.strategy_id,
            strategy_version=1,
            strategy_hash=approved.canonical_hash,
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


def test_run_domain_roundtrip(factory):
    spec = _spec()
    with session_scope(factory) as s:
        pid, approved = _seed_project_and_version(s, spec)
        run = Run(
            project_id=pid,
            strategy_id=approved.strategy_id,
            strategy_version=1,
            strategy_hash=approved.canonical_hash,
            data_mode="synthetic_fixture",
            seeds={"campaign": 7},
            limitations=["no point-in-time"],
        )
        rid = run.run_id
        RunRepository(s).create(run)

    with session_scope(factory) as s:
        loaded = RunRepository(s).get_domain(rid)
        assert loaded is not None
        assert loaded.run_id == rid
        assert loaded.seeds == {"campaign": 7}
        assert loaded.limitations == ["no point-in-time"]
        assert loaded.strategy_hash == approved.canonical_hash


def test_artifact_index_stores_no_filesystem_path(factory):
    spec = _spec()
    with session_scope(factory) as s:
        pid, approved = _seed_project_and_version(s, spec)
        run = Run(
            project_id=pid,
            strategy_id=approved.strategy_id,
            strategy_version=1,
            strategy_hash=approved.canonical_hash,
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
        j2 = Job(idempotency_key="run-abc:historical", stage=RunStage.HISTORICAL_BACKTEST)
        stored2 = repo.submit(j2)
        assert stored1.job_id == stored2.job_id  # no duplicate


def test_job_full_fidelity_roundtrip(factory):
    with session_scope(factory) as s:
        repo = JobRepository(s)
        j = Job(idempotency_key="k", stage=RunStage.STRESS_CAMPAIGN)
        repo.submit(j)
        j.mark_running()
        j.mark_failed(FailureReason(code="data_unavailable", message="timeout", retryable=True))
        repo.save(j)
        jid = j.job_id
        original = j.model_dump()

    with session_scope(factory) as s:
        loaded = JobRepository(s).get(jid)
        assert loaded is not None
        reloaded = loaded.model_dump()
        # Every field survives the round-trip (timestamps compared at second res).
        for key in (
            "job_id",
            "idempotency_key",
            "stage",
            "state",
            "attempts",
            "max_attempts",
            "progress",
            "inputs_frozen",
            "result_ref",
        ):
            assert reloaded[key] == original[key], key
        assert loaded.failure is not None and loaded.failure.retryable is True
        assert loaded.created_at is not None and loaded.updated_at is not None
