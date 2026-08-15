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
    dataset_digest: Mapped[str] = mapped_column(String(64), nullable=False, default="")
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


class DatasetRow(Base):
    """Frozen canonical dataset (market-data panel) registry (Phase 3 D5).

    Every acquired panel is recorded once per (project_id, canonical_digest).
    Runs reference the digest via ``RunRow.dataset_digest`` so a campaign can
    prove it replayed the EXACT frozen baseline the originating backtest used.
    """

    __tablename__ = "datasets"
    __table_args__ = (
        Index("ix_datasets_canonical_digest", "canonical_digest"),
        Index("ix_datasets_project_id", "project_id"),
        UniqueConstraint("project_id", "canonical_digest", name="uq_datasets_project_digest"),
    )

    dataset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    canonical_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_version: Mapped[str] = mapped_column(String(64), nullable=False)
    request_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    provenance_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    quality_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    artifact_manifest_ref: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


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


# --------------------------------------------------------------------------
# Phase 2.6 integrity-closure tables
# --------------------------------------------------------------------------
class IdempotencyRecordRow(Base):
    """Durable request-idempotency record (Phase 2.6 section 3).

    ``(scope, project_id, idempotency_key)`` is unique. A replay with the same
    request digest returns the recorded resource + response; a replay with a
    DIFFERENT digest is a conflict (HTTP 409). Concurrency-safe on Postgres via
    the unique constraint + IntegrityError recovery.
    """

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("scope", "project_id", "idempotency_key", name="idempotency_scope_project_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)  # approve|backtest|campaign
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)  # strategy_version|run|campaign
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    response_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CampaignRow(Base):
    """Durable synthetic-stress campaign, keyed by an unambiguous campaign_id and
    bound to the (project, strategy, version, hash) identity tuple."""

    __tablename__ = "campaigns"
    __table_args__ = (
        Index("ix_campaigns_strategy_hash", "strategy_hash"),
        Index("ix_campaigns_project_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_panel_digest: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    mechanisms: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    seeds: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    failure_predicates: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    confirmation_policy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    requested_worlds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evaluated_worlds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    predicate_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evaluation_errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    errors_by_mechanism: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    failure_rate_by_mechanism: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result_manifest_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ScenarioWorldRow(Base):
    """A generated, reproducible stress world (scenario definition + digest)."""

    __tablename__ = "scenario_worlds"
    __table_args__ = (
        UniqueConstraint("campaign_id", "world_key", name="campaign_world_key"),
        Index("ix_scenario_worlds_campaign_id", "campaign_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    world_key: Mapped[str] = mapped_column(String(128), nullable=False)
    mechanism: Mapped[str] = mapped_column(String(64), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    intensity: Mapped[float] = mapped_column(Float, nullable=False)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    diagnostics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WorldEvaluationRow(Base):
    """The outcome of evaluating the approved strategy on one scenario world."""

    __tablename__ = "world_evaluations"
    __table_args__ = (Index("ix_world_evaluations_campaign_id", "campaign_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    world_id: Mapped[str] = mapped_column(ForeignKey("scenario_worlds.id"), nullable=False)
    outcome: Mapped[str] = mapped_column(
        String(24), nullable=False
    )  # succeeded|failed_predicate|evaluation_error
    predicate_results: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(
        String(24), nullable=False, default="primary"
    )  # primary|confirmation|minimization|adjacent
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MinimizationTrialRow(Base):
    """One trial in the minimization search (bisection or grid)."""

    __tablename__ = "minimization_trials"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    failure_id: Mapped[str] = mapped_column(ForeignKey("world_evaluations.id"), nullable=False)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    failed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    predicate_results: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class AdjacentPassRow(Base):
    """A verified adjacent passing scenario (all failure predicates false)."""

    __tablename__ = "adjacent_passes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    failure_id: Mapped[str] = mapped_column(ForeignKey("world_evaluations.id"), nullable=False)
    scenario_id: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    predicate_results: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    artifact_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


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
    "IdempotencyRecordRow",
    "CampaignRow",
    "ScenarioWorldRow",
    "WorldEvaluationRow",
    "MinimizationTrialRow",
    "AdjacentPassRow",
]
