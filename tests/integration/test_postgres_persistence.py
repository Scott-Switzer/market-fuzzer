"""PostgreSQL-specific persistence tests (reset brief Phase 1.1 item 12).

SQLite is fine for fast unit tests, but the production database is PostgreSQL, so
the relational invariants (FK enforcement, CHECK constraints, unique constraints,
concurrent idempotent submit, tz-aware timestamps, JSON round-trip) must be
proven on real Postgres.

These tests are skipped unless ``FENRIX_TEST_POSTGRES_URL`` is set (CI sets it to
the ``services: postgres`` container; locally you can point it at a docker PG).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

import pytest

pytest.importorskip("psycopg")

PG_URL = os.environ.get("FENRIX_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not PG_URL, reason="FENRIX_TEST_POSTGRES_URL not set; skipping real-Postgres tests"
)

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.domain.run import Job, Run, RunStage  # noqa: E402
from app.domain.strategy_spec import StrategySpec, StrategyType  # noqa: E402
from app.domain.strategy_version import DraftStrategy  # noqa: E402
from app.evidence.artifact_store import ArtifactRef  # noqa: E402
from app.persistence.database import (  # noqa: E402
    create_all,
    make_engine,
    make_session_factory,
    session_scope,
)
from app.persistence.models import Base  # noqa: E402
from app.persistence.repositories import (  # noqa: E402
    JobRepository,
    ProjectRepository,
    RunRepository,
    StrategyRepository,
)

SUPPORTED = set(StrategyType) - {StrategyType.UNSUPPORTED}


@pytest.fixture
def pg_factory():
    engine = make_engine(PG_URL)
    # Fresh schema per test module run.
    Base.metadata.drop_all(engine)
    create_all(engine)
    yield make_session_factory(engine)
    Base.metadata.drop_all(engine)
    engine.dispose()


def _spec() -> StrategySpec:
    return StrategySpec(
        name="Momentum",
        original_thesis="top momentum names monthly",
        strategy_type=StrategyType.CROSS_SECTIONAL_FACTOR,
        universe=["AAPL", "MSFT", "NVDA"],
        benchmark="SPY",
    )


def _seed(s, spec: StrategySpec):
    proj = ProjectRepository(s).create(name="P")
    approved = DraftStrategy(spec=spec).approve(approved_by="scott", supported_types=SUPPORTED)
    StrategyRepository(s).create(proj.id, approved.strategy_id, spec.name, spec.original_thesis)
    StrategyRepository(s).add_approved_version(approved)
    return proj.id, approved


def test_pg_schema_creation(pg_factory):
    with session_scope(pg_factory) as s:
        names = set(inspect(s.get_bind()).get_table_names())
    assert {"projects", "strategies", "strategy_versions", "runs", "jobs"} <= names


def test_pg_fk_enforced_on_run(pg_factory):
    # runs has a composite FK to strategy_versions; inserting a run for a
    # non-existent version must raise IntegrityError on Postgres.
    with pytest.raises(IntegrityError):
        with session_scope(pg_factory) as s:
            ProjectRepository(s).create(name="P", project_id="p1")
            run = Run(
                project_id="p1",
                strategy_id="does-not-exist",
                strategy_version=1,
                strategy_hash="0" * 64,
                data_mode="synthetic_fixture",
            )
            RunRepository(s).create(run)


def test_pg_check_constraint_progress(pg_factory):
    with pytest.raises(IntegrityError):
        with session_scope(pg_factory) as s:
            s.execute(
                text(
                    "INSERT INTO jobs (id, idempotency_key, stage, state, attempts, "
                    "max_attempts, progress, inputs_frozen, created_at, updated_at) "
                    "VALUES (:id, :k, 'historical_backtest', 'queued', 0, 3, 2.0, false, "
                    "now(), now())"
                ),
                {"id": str(uuid.uuid4()), "k": str(uuid.uuid4())},
            )


def test_pg_check_constraint_bad_state(pg_factory):
    with pytest.raises(IntegrityError):
        with session_scope(pg_factory) as s:
            s.execute(
                text(
                    "INSERT INTO jobs (id, idempotency_key, stage, state, attempts, "
                    "max_attempts, progress, inputs_frozen, created_at, updated_at) "
                    "VALUES (:id, :k, 'historical_backtest', 'bogus', 0, 3, 0.5, false, "
                    "now(), now())"
                ),
                {"id": str(uuid.uuid4()), "k": str(uuid.uuid4())},
            )


def test_pg_duplicate_strategy_version_rejected(pg_factory):
    spec = _spec()
    with session_scope(pg_factory) as s:
        _, approved = _seed(s, spec)
    with pytest.raises(IntegrityError):
        with session_scope(pg_factory) as s:
            # same (strategy_id, version) again
            StrategyRepository(s).add_approved_version(approved)


def test_pg_duplicate_artifact_key_rejected(pg_factory):
    spec = _spec()
    with session_scope(pg_factory) as s:
        pid, approved = _seed(s, spec)
        run = Run(
            project_id=pid,
            strategy_id=approved.strategy_id,
            strategy_version=1,
            strategy_hash=approved.canonical_hash,
            data_mode="synthetic_fixture",
        )
        RunRepository(s).create(run)
        ref = ArtifactRef(store="filesystem", key="run/x/m.json", sha256="a" * 64, size=3)
        RunRepository(s).index_artifact(run.run_id, ref)
        rid = run.run_id
    with pytest.raises(IntegrityError):
        with session_scope(pg_factory) as s:
            ref = ArtifactRef(store="filesystem", key="run/x/m.json", sha256="a" * 64, size=3)
            RunRepository(s).index_artifact(rid, ref)


def test_pg_concurrent_idempotent_submit_single_job(pg_factory):
    """Two independent sessions race to submit the same idempotency key.
    Exactly one job row must exist and both callers get the same job_id."""
    key = f"race:{uuid.uuid4()}"

    s1 = pg_factory()
    s2 = pg_factory()
    try:
        j1 = Job(idempotency_key=key, stage=RunStage.HISTORICAL_BACKTEST)
        j2 = Job(idempotency_key=key, stage=RunStage.HISTORICAL_BACKTEST)
        # Both insert before either commits -> unique constraint forces one to
        # lose and recover via the IntegrityError path.
        r1 = JobRepository(s1).submit(j1)
        s1.commit()
        r2 = JobRepository(s2).submit(j2)
        s2.commit()
        assert r1.job_id == r2.job_id
    finally:
        s1.close()
        s2.close()

    with session_scope(pg_factory) as s:
        count = s.execute(
            text("SELECT count(*) FROM jobs WHERE idempotency_key = :k"), {"k": key}
        ).scalar_one()
        assert count == 1


def test_pg_json_and_tz_roundtrip(pg_factory):
    spec = _spec()
    with session_scope(pg_factory) as s:
        pid, approved = _seed(s, spec)
        run = Run(
            project_id=pid,
            strategy_id=approved.strategy_id,
            strategy_version=1,
            strategy_hash=approved.canonical_hash,
            data_mode="synthetic_fixture",
            seeds={"a": 1, "b": 2},
            limitations=["x", "y"],
        )
        rid = run.run_id
        RunRepository(s).create(run)
    with session_scope(pg_factory) as s:
        row = RunRepository(s).get(rid)
        assert row is not None
        assert row.seeds == {"a": 1, "b": 2}
        assert row.limitations == ["x", "y"]
        assert isinstance(row.created_at, datetime)
        assert row.created_at.tzinfo is not None  # timezone-aware
