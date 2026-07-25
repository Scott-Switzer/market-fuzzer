"""Database engine/session setup (SQLAlchemy 2).

Postgres in production via ``FENRIX_DATABASE_URL``; defaults to a local SQLite
file for development and in-memory SQLite for tests. Alembic owns schema
migrations in production; ``create_all`` is used only for tests/local bootstrap.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.models import Base

DEFAULT_URL = "sqlite:///./fenrix.db"


def database_url() -> str:
    return os.environ.get("FENRIX_DATABASE_URL", DEFAULT_URL)


def make_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    url = url or database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, echo=echo, future=True, connect_args=connect_args)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def create_all(engine: Engine) -> None:
    """Test/local bootstrap only. Production uses Alembic migrations."""
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transactional scope: commit on success, rollback on error."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = [
    "DEFAULT_URL",
    "database_url",
    "make_engine",
    "make_session_factory",
    "create_all",
    "session_scope",
]
