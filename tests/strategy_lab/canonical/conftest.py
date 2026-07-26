"""Fixtures for canonical v2 API tests (Phase 2.5).

Builds a FastAPI app mounting ONLY the canonical v2 router, backed by a temp
SQLite database. The get_session dependency is overridden per test so tests are
isolated and can simulate application restarts by reopening the same DB file.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.persistence.database import create_all, make_engine, make_session_factory
from app.strategy_lab.canonical import router as canonical_router
from app.strategy_lab.canonical.router import get_session


@pytest.fixture()
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 'fenrix_test.db'}"
    engine = make_engine(url)
    create_all(engine)
    factory = make_session_factory(engine)
    return {"engine": engine, "factory": factory, "url": url}


def _make_client(factory) -> TestClient:
    app = FastAPI()
    app.include_router(canonical_router)

    def _override():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


@pytest.fixture()
def client(db):
    return _make_client(db["factory"])


@pytest.fixture()
def reopen(db):
    """Return a callable that opens a fresh app+session against the same DB file.

    Used to simulate an application restart: state must survive in the DB, not in
    process memory.
    """

    def _reopen():
        engine = make_engine(db["url"])
        factory = make_session_factory(engine)
        return _make_client(factory)

    return _reopen
