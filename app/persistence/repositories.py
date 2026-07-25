"""Repositories: the only sanctioned way to read/write domain objects to the DB.

Keeps SQLAlchemy rows out of the rest of the app -- callers exchange domain
objects (StrategySpec, Run, Job, ...). Enforces the durable-run and
idempotency guarantees from reset brief section 11.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.run import FailureReason, Job, JobState, Run, RunStage
from app.domain.strategy_spec import StrategySpec
from app.domain.strategy_version import ApprovalState, StrategyVersion
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

    def create(self, project_id: str, spec: StrategySpec) -> Strategy:
        row = Strategy(
            id=spec.strategy_id,
            project_id=project_id,
            name=spec.name,
            original_thesis=spec.original_thesis,
        )
        self.s.add(row)
        self.s.flush()
        return row

    def add_version(self, version: StrategyVersion) -> StrategyVersionRow:
        row = StrategyVersionRow(
            strategy_id=version.strategy_id,
            version=version.version,
            canonical_hash=version.canonical_hash,
            spec_json=version.spec.model_dump(mode="json"),
            state=version.state.value,
            approved_by=version.approved_by,
            approved_at=version.approved_at,
        )
        self.s.add(row)
        self.s.flush()
        return row

    def get_version(self, strategy_id: str, version: int) -> StrategyVersion | None:
        row = self.s.scalar(
            select(StrategyVersionRow).where(
                StrategyVersionRow.strategy_id == strategy_id,
                StrategyVersionRow.version == version,
            )
        )
        if row is None:
            return None
        return StrategyVersion(
            strategy_id=row.strategy_id,
            version=row.version,
            canonical_hash=row.canonical_hash,
            spec=StrategySpec.model_validate(row.spec_json),
            state=ApprovalState(row.state),
            approved_by=row.approved_by,
            approved_at=row.approved_at,
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
        """Idempotent submit: if a job with the same idempotency_key exists,
        return the existing one instead of inserting a duplicate (gate: duplicate
        submission protection)."""
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
            failure_json=job.failure.model_dump(mode="json") if job.failure else None,
            result_ref=job.result_ref,
        )
        self.s.add(row)
        self.s.flush()
        return self._to_domain(row)

    def save(self, job: Job) -> None:
        row = self.s.get(JobRow, job.job_id)
        if row is None:
            raise KeyError(job.job_id)
        row.state = job.state.value
        row.attempts = job.attempts
        row.progress = job.progress
        row.failure_json = job.failure.model_dump(mode="json") if job.failure else None
        row.result_ref = job.result_ref
        self.s.flush()

    def get(self, job_id: str) -> Job | None:
        row = self.s.get(JobRow, job_id)
        return self._to_domain(row) if row else None

    @staticmethod
    def _to_domain(row: JobRow) -> Job:
        return Job(
            job_id=row.id,
            idempotency_key=row.idempotency_key,
            stage=RunStage(row.stage),
            state=JobState(row.state),
            attempts=row.attempts,
            max_attempts=row.max_attempts,
            progress=row.progress,
            failure=FailureReason.model_validate(row.failure_json) if row.failure_json else None,
            result_ref=row.result_ref,
        )


__all__ = [
    "ProjectRepository",
    "StrategyRepository",
    "RunRepository",
    "JobRepository",
]
