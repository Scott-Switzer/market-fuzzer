"""Alembic migration round-trip test (reset brief section 17: database migration tests).

Proves the committed migration builds the exact schema the models declare, and
that upgrade -> downgrade -> upgrade is clean. Runs against a temp SQLite file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TABLES = {
    "projects",
    "strategies",
    "strategy_versions",
    "runs",
    "jobs",
    "failures",
    "artifact_index",
    "evidence_verification",
    "data_sources",
}


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "app" / "persistence" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture
def db_url(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    monkeypatch.setenv("FENRIX_DATABASE_URL", url)
    return url


def test_upgrade_creates_expected_tables(db_url):
    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= tables


def test_downgrade_then_upgrade_is_clean(db_url):
    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    engine = create_engine(db_url)
    remaining = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert remaining == set(), f"downgrade left tables behind: {remaining}"
    command.upgrade(cfg, "head")
    tables = set(inspect(create_engine(db_url)).get_table_names())
    assert EXPECTED_TABLES <= tables


def test_migration_matches_models_metadata(db_url):
    """The migrated schema must contain every table the ORM Base declares."""
    from app.persistence.models import Base

    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    migrated = set(inspect(engine).get_table_names())
    declared = set(Base.metadata.tables.keys())
    assert declared <= migrated, f"models declare tables not in migration: {declared - migrated}"
