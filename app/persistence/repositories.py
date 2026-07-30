"""Repositories: the only sanctioned way to read/write domain objects to the DB.

Keeps SQLAlchemy rows out of the rest of the app -- callers exchange domain
objects (StrategySpec, Run, Job, ...). Enforces the durable-run and idempotency
guarantees from reset brief section 11 and the Phase 1.1 fidelity/concurrency fixes.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.run import FailureReason, Job, JobState, Run, RunStage, RunStatus
from app.domain.strategy_spec import StrategySpec
from app.domain.strategy_version import ApprovedStrategyVersion
from app.evidence.artifact_store import ArtifactRef
from app.persistence.models import (
    ArtifactIndexRow,
    JobRow,
    Project,
    RunRow,
    Strategy,
    StrategyVersionRow,
)


class ProjectRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def create(self, name: str, owner: str = "local", project_id: str | None = None) -> Project:
        p = Project(id=project_id or str(uuid.uuid4()), name=name, owner=owner)
        self.s.add(p)
        self.s.flush()
        return p

    def get(self, project_id: str) -> Project | None:
        return self.s.get(Project, project_id)

    def list(self) -> list[Project]:
        return list(self.s.scalars(select(Project).order_by(Project.created_at.desc())))


class StrategyRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def create(self, project_id: str, strategy_id: str, name: str, original_thesis: str) -> Strategy:
        row = Strategy(
            id=strategy_id,
            project_id=project_id,
            name=name,
            original_thesis=original_thesis,
        )
        self.s.add(row)
        self.s.flush()
        return row

    def add_approved_version(self, approved: ApprovedStrategyVersion) -> StrategyVersionRow:
        """Persist an immutable approved version. Stores canonical_json + hash
        as the durable source of truth (no lossy re-serialization)."""
        row = StrategyVersionRow(
            strategy_id=approved.strategy_id,
            version=approved.version,
            canonical_hash=approved.canonical_hash,
            canonical_json=approved.canonical_json,
            schema_version=approved.schema_version,
            state="approved",
            approved_by=approved.approved_by,
            approved_at=approved.approved_at,
        )
        self.s.add(row)
        self.s.flush()
        return row

    def get_approved_version(self, strategy_id: str, version: int) -> ApprovedStrategyVersion | None:
        row = self.s.scalar(
            select(StrategyVersionRow).where(
                StrategyVersionRow.strategy_id == strategy_id,
                StrategyVersionRow.version == version,
            )
        )
        if row is None:
            return None
        return self._version_to_domain(row)

    def get_spec(self, strategy_id: str, version: int) -> StrategySpec | None:
        """Reconstruct the executable spec, re-verifying its hash (tamper-safe)."""
        approved = self.get_approved_version(strategy_id, version)
        return approved.to_spec() if approved else None

    @staticmethod
    def _version_to_domain(row: StrategyVersionRow) -> ApprovedStrategyVersion:
        return ApprovedStrategyVersion(
            strategy_id=row.strategy_id,
            version=row.version,
            schema_version=row.schema_version,
            canonical_hash=row.canonical_hash,
            canonical_json=row.canonical_json,
            approved_by=row.approved_by or "",
            approved_at=row.approved_at,  # type: ignore[arg-type]
        )


class RunRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def create(self, run: Run) -> RunRow:
        row = RunRow(
            id=run.run_id,
            project_id=run.project_id,
            strategy_id=run.strategy_id,
            strategy_version=run.strategy_version,
            strategy_hash=run.strategy_hash,
            data_mode=run.data_mode,
            status=run.status.value,
            stages_completed=[s.value for s in run.stages_completed],
            seeds=run.seeds,
            limitations=run.limitations,
        )
        self.s.add(row)
        self.s.flush()
        return row

    def get(self, run_id: str) -> RunRow | None:
        return self.s.get(RunRow, run_id)

    def get_domain(self, run_id: str) -> Run | None:
        row = self.s.get(RunRow, run_id)
        if row is None:
            return None
        return Run(
            run_id=row.id,
            project_id=row.project_id,
            strategy_id=row.strategy_id,
            strategy_version=row.strategy_version,
            strategy_hash=row.strategy_hash,
            data_mode=row.data_mode,
            status=RunStatus(row.status),
            stages_completed=[RunStage(s) for s in row.stages_completed],
            created_at=row.created_at,
            seeds=dict(row.seeds),
            limitations=list(row.limitations),
        )

    def mark_stage(self, run_id: str, stage: RunStage) -> None:
        row = self.s.get(RunRow, run_id)
        if row is None:
            raise KeyError(run_id)
        stages = list(row.stages_completed)
        if stage.value not in stages:
            stages.append(stage.value)
            row.stages_completed = stages
        self.s.flush()

    def index_artifact(self, run_id: str, ref: ArtifactRef) -> ArtifactIndexRow:
        row = ArtifactIndexRow(
            run_id=run_id,
            store=ref.store,
            artifact_key=ref.key,
            sha256=ref.sha256,
            size=ref.size,
            content_type=ref.content_type,
        )
        self.s.add(row)
        self.s.flush()
        return row


class JobRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def submit(self, job: Job, run_id: str | None = None) -> Job:
        """Concurrency-safe idempotent submit.

        The DB unique constraint on ``idempotency_key`` is the final authority.
        We attempt the insert inside a SAVEPOINT; on ``IntegrityError`` (a
        concurrent inserter won the race) we roll back only that savepoint and
        return the existing row. Never creates two jobs; never surfaces a 500 on
        a duplicate submission.
        """
        # Fast path: already present.
        existing = self.s.scalar(select(JobRow).where(JobRow.idempotency_key == job.idempotency_key))
        if existing is not None:
            return self._to_domain(existing)

        row = JobRow(
            id=job.job_id,
            run_id=run_id,
            idempotency_key=job.idempotency_key,
            stage=job.stage.value,
            state=job.state.value,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            progress=job.progress,
            inputs_frozen=job.inputs_frozen,
            failure_json=job.failure.model_dump(mode="json") if job.failure else None,
            result_ref=job.result_ref,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
        try:
            with self.s.begin_nested():  # SAVEPOINT
                self.s.add(row)
                self.s.flush()
        except IntegrityError:
            # Lost the race: another transaction inserted the same key. The
            # savepoint is already rolled back; fetch and return the winner.
            existing = self.s.scalar(select(JobRow).where(JobRow.idempotency_key == job.idempotency_key))
            if existing is None:  # pragma: no cover - defensive
                raise
            return self._to_domain(existing)
        return self._to_domain(row)

    def save(self, job: Job) -> None:
        row = self.s.get(JobRow, job.job_id)
        if row is None:
            raise KeyError(job.job_id)
        row.state = job.state.value
        row.attempts = job.attempts
        row.progress = job.progress
        row.inputs_frozen = job.inputs_frozen
        row.failure_json = job.failure.model_dump(mode="json") if job.failure else None
        row.result_ref = job.result_ref
        row.updated_at = job.updated_at
        self.s.flush()

    def get(self, job_id: str) -> Job | None:
        row = self.s.get(JobRow, job_id)
        return self._to_domain(row) if row else None

    @staticmethod
    def _to_domain(row: JobRow) -> Job:
        # Full fidelity: restore every field, including timestamps and
        # inputs_frozen, so a persisted->reloaded job is semantically identical.
        return Job(
            job_id=row.id,
            idempotency_key=row.idempotency_key,
            stage=RunStage(row.stage),
            state=JobState(row.state),
            attempts=row.attempts,
            max_attempts=row.max_attempts,
            progress=row.progress,
            inputs_frozen=row.inputs_frozen,
            failure=FailureReason.model_validate(row.failure_json) if row.failure_json else None,
            result_ref=row.result_ref,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


__all__ = [
    "ProjectRepository",
    "StrategyRepository",
    "RunRepository",
    "JobRepository",
]
