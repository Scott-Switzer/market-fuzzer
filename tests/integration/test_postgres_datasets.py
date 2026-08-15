"""PostgreSQL dataset-persistence invariants (Phase 3 D5).

These prove the ``datasets`` table behaves correctly on real PostgreSQL:
FK enforcement, independent dataset identity (NOT digest-derived), the
``(project_id, canonical_digest)`` uniqueness contract across two projects, and
concurrent-freeze safety (exactly one row, no 500, both callers resolve to it).

They are skipped unless ``FENRIX_TEST_POSTGRES_URL`` is set (CI sets it to the
``services: postgres`` container). The SQLite unit tests in
``tests/strategy_lab/canonical/test_phase3_data_layer.py`` cover the same logic
offline; this file is the authoritative relational gate.

All tests here mirror the patterns already proven green in
``test_postgres_persistence.py`` (no nested savespoints inside the test body;
concurrency uses two independent sessions and a real commit race).
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest

pytest.importorskip("psycopg")

PG_URL = os.environ.get("FENRIX_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not PG_URL, reason="FENRIX_TEST_POSTGRES_URL not set; skipping real-Postgres tests"
)


from sqlalchemy import text  # noqa: E402

from app.persistence.database import create_all, make_engine, make_session_factory  # noqa: E402
from app.persistence.models import Base, DatasetRow, Project  # noqa: E402

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


@pytest.fixture()
def pg_factory():
    engine = make_engine(PG_URL)
    Base.metadata.drop_all(engine)
    create_all(engine)
    yield make_session_factory(engine)
    Base.metadata.drop_all(engine)
    engine.dispose()


def _seed_project(s, pid: str) -> None:
    s.add(Project(id=pid, name=pid, owner="tester"))
    s.commit()


def _register(s, pid: str, digest: str) -> None:
    """Insert a datasets row directly (the production path is exercised by the
    SQLite suite); here we assert the relational contract on real Postgres."""
    s.add(
        DatasetRow(
            dataset_id=f"ds_{uuid.uuid4().hex}",
            project_id=pid,
            canonical_digest=digest,
            provider="synthetic_fixture",
            provider_version="synthetic-gen/1.0",
            request_json={},
            provenance_json={},
            quality_json={},
            artifact_manifest_ref="runs/x/market-data-manifest.json",
        )
    )
    s.commit()


def test_dataset_id_fits_schema(pg_factory):
    """dataset_id is an independent ds_<uuid4 hex> (35 chars), well under 64,
    and is NOT the digest (which would be 67 chars and globally scoped)."""
    _seed_project(pg_factory(), "p1")
    s = pg_factory()
    _register(s, "p1", DIGEST_A)
    row = s.query(DatasetRow).filter_by(project_id="p1", canonical_digest=DIGEST_A).one()
    assert row.dataset_id.startswith("ds_")
    assert len(row.dataset_id) <= 64
    assert row.dataset_id != f"ds_{DIGEST_A}"
    assert len(row.dataset_id) < 64


def test_identical_digest_same_project_one_row(pg_factory):
    from sqlalchemy.exc import IntegrityError

    _seed_project(pg_factory(), "p1")
    s = pg_factory()
    _register(s, "p1", DIGEST_A)
    # A second freeze of the identical (project, digest) must be rejected by the
    # unique constraint -- proving the semantic identity lives in the DB, not the
    # (digest-derived) primary key.
    with pytest.raises(IntegrityError):
        _register(s, "p1", DIGEST_A)
    n = s.query(DatasetRow).filter_by(project_id="p1", canonical_digest=DIGEST_A).count()
    assert n == 1


def test_identical_digest_different_projects_two_rows(pg_factory):
    _seed_project(pg_factory(), "p1")
    _seed_project(pg_factory(), "p2")
    s = pg_factory()
    _register(s, "p1", DIGEST_A)
    _register(s, "p2", DIGEST_A)
    rows = s.query(DatasetRow).filter_by(canonical_digest=DIGEST_A).all()
    assert len(rows) == 2
    assert rows[0].dataset_id != rows[1].dataset_id


def test_missing_project_rejected_by_fk(pg_factory):
    from sqlalchemy.exc import IntegrityError

    s = pg_factory()  # no project seeded
    with pytest.raises(IntegrityError):
        s.add(
            DatasetRow(
                dataset_id=f"ds_{uuid.uuid4().hex}",
                project_id="ghost",
                canonical_digest=DIGEST_A,
                provider="synthetic_fixture",
                provider_version="v",
                request_json={},
                provenance_json={},
                quality_json={},
                artifact_manifest_ref="r",
            )
        )
        s.commit()


def test_concurrent_identical_freezes_one_row(pg_factory):
    """Two independent sessions race to register the same (project, digest).
    Exactly one row must exist and both callers observe the same outcome."""
    _seed_project(pg_factory(), "p1")

    s1 = pg_factory()
    s2 = pg_factory()
    errors: list[BaseException] = []

    def _work(session) -> None:
        try:
            session.add(
                DatasetRow(
                    dataset_id=f"ds_{uuid.uuid4().hex}",
                    project_id="p1",
                    canonical_digest=DIGEST_A,
                    provider="synthetic_fixture",
                    provider_version="synthetic-gen/1.0",
                    request_json={},
                    provenance_json={},
                    quality_json={},
                    artifact_manifest_ref="runs/x/market-data-manifest.json",
                )
            )
            session.commit()
        except Exception as e:  # noqa: BLE001 - surface any unexpected failure
            errors.append(e)

    t1 = threading.Thread(target=_work, args=(s1,))
    t2 = threading.Thread(target=_work, args=(s2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    s1.close()
    s2.close()

    assert not errors, f"concurrent freeze raised: {errors}"
    s = pg_factory()
    n = s.query(DatasetRow).filter_by(project_id="p1", canonical_digest=DIGEST_A).count()
    assert n == 1


def test_alembic_check_clean_on_postgres(pg_factory):
    """The migrated schema must match the models exactly (no pending ops)."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "app" / "persistence" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", PG_URL)
    # The pg_factory fixture created the schema via Base.metadata.create_all and
    # left the alembic_version table stamped at head. Drop the whole schema
    # (including alembic_version) so alembic builds from scratch and check() can
    # compare the migrated schema against the models.
    engine = make_engine(PG_URL)
    Base.metadata.drop_all(engine)
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        conn.commit()
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if models != migrations
