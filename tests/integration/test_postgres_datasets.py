"""PostgreSQL dataset-persistence invariants (Phase 3 D5).

These prove the ``datasets`` table behaves correctly on real PostgreSQL:
FK enforcement, independent dataset identity (NOT digest-derived), the
``(project_id, canonical_digest)`` uniqueness contract across two projects, and
concurrent-freeze safety (exactly one row, no 500, both callers resolve to it).

They are skipped unless ``FENRIX_TEST_POSTGRES_URL`` is set (CI sets it to the
``services: postgres`` container). The SQLite unit tests in
``tests/strategy_lab/canonical/test_phase3_data_layer.py`` cover the same logic
offline; this file is the authoritative relational gate.
"""

from __future__ import annotations

import os
import threading

import pytest

pytest.importorskip("psycopg")

PG_URL = os.environ.get("FENRIX_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not PG_URL, reason="FENRIX_TEST_POSTGRES_URL not set; skipping real-Postgres tests"
)


from app.persistence.database import create_all, make_engine, make_session_factory  # noqa: E402
from app.persistence.models import Base, DatasetRow, Project  # noqa: E402

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def _fresh(engine):
    Base.metadata.drop_all(engine)
    create_all(engine)


@pytest.fixture()
def pg():
    engine = make_engine(PG_URL)
    _fresh(engine)
    yield make_session_factory(engine)
    _fresh(engine)
    engine.dispose()


def _seed_project(s, pid: str) -> None:
    s.add(Project(id=pid, name=pid, owner="tester"))
    s.commit()


def test_dataset_id_fits_schema(pg):
    """dataset_id is an independent ds_<uuid4 hex> (35 chars), well under 64."""
    from app.market_data.artifacts import _register_dataset

    _seed_project(pg(), "p1")
    s = pg()
    _register_dataset(
        s,
        project_id="p1",
        canonical_digest=DIGEST_A,
        provider="synthetic_fixture",
        provider_version="synthetic-gen/1.0",
        request_json={},
        provenance_json={},
        quality_json={},
        artifact_manifest_ref="runs/x/market-data-manifest.json",
    )
    s.commit()
    row = s.query(DatasetRow).filter_by(project_id="p1", canonical_digest=DIGEST_A).one()
    assert row.dataset_id.startswith("ds_")
    assert len(row.dataset_id) <= 64
    assert row.dataset_id != f"ds_{DIGEST_A}"  # NOT digest-derived
    assert len(row.dataset_id) < 64  # independent identity


def test_identical_digest_same_project_one_row(pg):
    from app.market_data.artifacts import _register_dataset

    _seed_project(pg(), "p1")
    s = pg()
    for _ in range(3):
        _register_dataset(
            s,
            project_id="p1",
            canonical_digest=DIGEST_A,
            provider="synthetic_fixture",
            provider_version="synthetic-gen/1.0",
            request_json={},
            provenance_json={},
            quality_json={},
            artifact_manifest_ref="runs/x/market-data-manifest.json",
        )
    s.commit()
    n = s.query(DatasetRow).filter_by(project_id="p1", canonical_digest=DIGEST_A).count()
    assert n == 1


def test_identical_digest_different_projects_two_rows(pg):
    from app.market_data.artifacts import _register_dataset

    _seed_project(pg(), "p1")
    _seed_project(pg(), "p2")
    s = pg()
    _register_dataset(
        s,
        project_id="p1",
        canonical_digest=DIGEST_A,
        provider="synthetic_fixture",
        provider_version="v",
        request_json={},
        provenance_json={},
        quality_json={},
        artifact_manifest_ref="r",
    )
    _register_dataset(
        s,
        project_id="p2",
        canonical_digest=DIGEST_A,
        provider="synthetic_fixture",
        provider_version="v",
        request_json={},
        provenance_json={},
        quality_json={},
        artifact_manifest_ref="r",
    )
    s.commit()
    # Two distinct projects, identical digest -> TWO distinct rows (distinct PKs).
    rows = s.query(DatasetRow).filter_by(canonical_digest=DIGEST_A).all()
    assert len(rows) == 2
    assert rows[0].dataset_id != rows[1].dataset_id


def test_missing_project_rejected_by_fk(pg):
    from sqlalchemy.exc import IntegrityError

    from app.market_data.artifacts import _register_dataset

    s = pg()  # no project seeded
    with pytest.raises(IntegrityError):
        _register_dataset(
            s,
            project_id="ghost",
            canonical_digest=DIGEST_A,
            provider="synthetic_fixture",
            provider_version="v",
            request_json={},
            provenance_json={},
            quality_json={},
            artifact_manifest_ref="r",
        )
        s.commit()


def test_concurrent_identical_freezes_one_row(pg):
    """Two simultaneous freezes of (p1, DIGEST_A) -> exactly one row, no crash."""
    from app.market_data.artifacts import _register_dataset

    _seed_project(pg(), "p1")
    errors: list[BaseException] = []

    def _work() -> None:
        try:
            s = pg()
            _register_dataset(
                s,
                project_id="p1",
                canonical_digest=DIGEST_A,
                provider="synthetic_fixture",
                provider_version="synthetic-gen/1.0",
                request_json={},
                provenance_json={},
                quality_json={},
                artifact_manifest_ref="runs/x/market-data-manifest.json",
            )
            s.commit()
            s.close()
        except Exception as e:  # noqa: BLE001 - surface any unexpected failure
            errors.append(e)

    t1 = threading.Thread(target=_work)
    t2 = threading.Thread(target=_work)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"concurrent freeze raised: {errors}"
    s = pg()
    n = s.query(DatasetRow).filter_by(project_id="p1", canonical_digest=DIGEST_A).count()
    assert n == 1


def test_alembic_check_clean_on_postgres(pg):
    """The migrated schema must match the models exactly (no pending ops)."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "app" / "persistence" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", PG_URL)
    # upgrade already applied by the fixture's create_all? No — drop_all ran;
    # apply via alembic so `check` compares migration vs models.
    _fresh(make_engine(PG_URL))
    command.upgrade(cfg, "head")
    # alembic check exits non-zero (raises) if models != migrations.
    command.check(cfg)
