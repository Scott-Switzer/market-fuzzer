"""SQLAlchemy 2 persistence models (reset architecture ``app/persistence/models.py``).

PostgreSQL is authoritative in production (reset brief section 11). Models are portable so
fast unit tests can run against SQLite, but the schema is proven on real
PostgreSQL in CI (``tests/integration/test_postgres_persistence.py``).

Hardening (reset brief Phase 1.1 item 11):

* A metadata naming convention names every constraint deterministically, which
  Alembic needs to emit stable ``ALTER``/``DROP`` on Postgres.
* ``runs`` carries a real foreign key to ``strategy_versions`` (identity + version),
  not a bare string.
* ``jobs`` has DB check constraints on attempts/progress and allowed enum values.
* ``artifact_index`` enforces uniqueness on ``(run_id, store, artifact_key)``.
* ``failures`` is indexed on ``(run_id, strategy_hash, mechanism)``.

Artifacts live in the ArtifactStore; the DB holds only an *index* (opaque keys +
hashes), never raw blobs and never local filesystem paths.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.domain.run import JobState, RunStage

# Deterministic constraint naming (SQLAlchemy docs: Configuring Constraint Naming
# Conventions). Required for stable Alembic autogenerate/downgrade on Postgres.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Allowed enum values enforced at the DB layer, derived from the domain enums so
# the check constraints can never drift from the state machine / stage set.
JOB_STATES = tuple(s.value for s in JobState)
JOB_STAGES = tuple(s.value for s in RunStage)


def _sql_in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False, default="local")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    strategies: Mapped[list[Strategy]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_thesis: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    project: Mapped[Project] = relationship(back_populates="strategies")
    versions: Mapped[list[StrategyVersionRow]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan"
    )


class StrategyVersionRow(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="strategy_version"),
        Index("ix_strategy_versions_canonical_hash", "canonical_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    canonical_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Immutable approved source of truth: the canonical JSON string + hash.
    canonical_json: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="strategy-spec/v1.1")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    strategy: Mapped[Strategy] = relationship(back_populates="versions")
    runs: Mapped[list[RunRow]] = relationship(back_populates="strategy_version_ref")


class RunRow(Base):
    __tablename__ = "runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["strategy_id", "strategy_version"],
            ["strategy_versions.strategy_id", "strategy_versions.version"],
            name="strategy_version_ref",
        ),
        Index("ix_runs_strategy_hash", "strategy_hash"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    strategy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    data_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="created")
    stages_completed: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    seeds: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    limitations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    strategy_version_ref: Mapped[StrategyVersionRow] = relationship(back_populates="runs")


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="idempotency_key"),
        CheckConstraint("attempts >= 0", name="attempts_nonneg"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_min"),
        CheckConstraint("attempts <= max_attempts", name="attempts_le_max"),
        CheckConstraint("progress >= 0 AND progress <= 1", name="progress_unit_interval"),
        CheckConstraint(f"state IN ({_sql_in_list(JOB_STATES)})", name="state_allowed"),
        CheckConstraint(
            f"stage IN ({_sql_in_list(JOB_STAGES)})",
            name="stage_allowed",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    inputs_frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    failure_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class FailureRow(Base):
    __tablename__ = "failures"
    __table_args__ = (
        Index("ix_failures_run_id", "run_id"),
        Index("ix_failures_strategy_hash", "strategy_hash"),
        Index("ix_failures_mechanism", "mechanism"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    strategy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    world_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mechanism: Mapped[str] = mapped_column(String(64), nullable=False)
    intensity: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    violated_predicates: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    seed_agreement: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    execution_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ArtifactIndexRow(Base):
    __tablename__ = "artifact_index"
    __table_args__ = (UniqueConstraint("run_id", "store", "artifact_key", name="run_store_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    store: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_key: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False, default="application/octet-stream")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EvidenceVerificationRow(Base):
    # One verification record per run (latest result retained; history is not
    # kept in v1.1 -- documented in docs/architecture/DOMAIN_MODEL.md).
    __tablename__ = "evidence_verification"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, unique=True)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class DataSourceRow(Base):
    __tablename__ = "data_sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tier: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    calendar: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    point_in_time: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    universe_available: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


__all__ = [
    "Base",
    "NAMING_CONVENTION",
    "JOB_STATES",
    "JOB_STAGES",
    "Project",
    "Strategy",
    "StrategyVersionRow",
    "RunRow",
    "JobRow",
    "FailureRow",
    "ArtifactIndexRow",
    "EvidenceVerificationRow",
    "DataSourceRow",
]
