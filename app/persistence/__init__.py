"""Fenrix persistence layer (reset architecture ``app/persistence/``).

PostgreSQL-authoritative in production; SQLite for local/tests. Alembic owns
migrations. Repositories are the sanctioned read/write path.
"""

from __future__ import annotations

from app.persistence.database import (
    create_all,
    database_url,
    make_engine,
    make_session_factory,
    session_scope,
)
from app.persistence.models import Base

__all__ = [
    "Base",
    "create_all",
    "database_url",
    "make_engine",
    "make_session_factory",
    "session_scope",
]
