"""Run / job lifecycle domain model.

Reset brief section 11: every long-running unit of work has a durable id, an idempotency
key, an explicit state machine, bounded retries, and a structured failure reason.
This module defines the *domain* objects; persistence (SQLAlchemy) and execution
(Celery) bind to them in later phases. The state machine here is the single
source of truth for legal transitions, usable by both the sync test executor and
the async worker.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunStage(StrEnum):
    """Stages of a full validation run (mirrors the OpenTelemetry trace tree)."""

    COMPILE = "compile"
    RESOLVE = "resolve"
    APPROVE = "approve"
    ACQUIRE_DATA = "acquire_data"
    HISTORICAL_BACKTEST = "historical_backtest"
    CALCULATE_METRICS = "calculate_metrics"
    STRESS_CAMPAIGN = "stress_campaign"
    MINIMIZE_FAILURE = "minimize_failure"
    ADJACENT_PASS_SEARCH = "adjacent_pass_search"
    EXCHANGE_REPLAY = "exchange_replay"
    EVIDENCE_EXPORT = "evidence_export"
    EVIDENCE_VERIFY = "evidence_verify"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Legal transitions. Fail-closed: anything not listed is illegal.
_LEGAL_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.RUNNING, JobState.CANCELLED}),
    JobState.RUNNING: frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}),
    JobState.SUCCEEDED: frozenset(),  # terminal
    JobState.FAILED: frozenset({JobState.QUEUED}),  # retry re-queues
    JobState.CANCELLED: frozenset(),  # terminal
}

TERMINAL_STATES: frozenset[JobState] = frozenset({JobState.SUCCEEDED, JobState.CANCELLED})


class IllegalTransition(ValueError):
    pass


def can_transition(src: JobState, dst: JobState) -> bool:
    return dst in _LEGAL_TRANSITIONS.get(src, frozenset())


class FailureReason(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str  # machine-readable, e.g. "data_unavailable", "validation_error"
    message: str
    retryable: bool = False  # deterministic validation errors are NOT retryable
    details: dict[str, Any] = Field(default_factory=dict)


class Job(BaseModel):
    """A durable unit of simulation work."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    idempotency_key: str
    stage: RunStage
    state: JobState = JobState.QUEUED
    attempts: int = 0
    max_attempts: int = 3
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    failure: FailureReason | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    inputs_frozen: bool = False
    result_ref: str | None = None  # ArtifactRef key or run id, committed atomically

    def transition(self, dst: JobState) -> None:
        if not can_transition(self.state, dst):
            raise IllegalTransition(f"illegal transition {self.state} -> {dst}")
        object.__setattr__(self, "state", dst)
        object.__setattr__(self, "updated_at", datetime.now(UTC))

    def mark_running(self) -> None:
        self.transition(JobState.RUNNING)
        object.__setattr__(self, "attempts", self.attempts + 1)

    def mark_succeeded(self, result_ref: str | None = None) -> None:
        self.transition(JobState.SUCCEEDED)
        object.__setattr__(self, "progress", 1.0)
        if result_ref is not None:
            object.__setattr__(self, "result_ref", result_ref)

    def mark_failed(self, reason: FailureReason) -> None:
        self.transition(JobState.FAILED)
        object.__setattr__(self, "failure", reason)

    def mark_cancelled(self) -> None:
        self.transition(JobState.CANCELLED)

    def can_retry(self) -> bool:
        """Retry only transient failures within the attempt budget."""
        return (
            self.state == JobState.FAILED
            and self.failure is not None
            and self.failure.retryable
            and self.attempts < self.max_attempts
        )

    def requeue_for_retry(self) -> None:
        if not self.can_retry():
            raise IllegalTransition("job is not eligible for retry")
        self.transition(JobState.QUEUED)


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Run(BaseModel):
    """A full validation run of a locked strategy version."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    strategy_id: str
    strategy_version: int
    strategy_hash: str
    data_mode: str
    status: RunStatus = RunStatus.CREATED
    stages_completed: list[RunStage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    seeds: dict[str, int] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)

    def complete_stage(self, stage: RunStage) -> None:
        if stage not in self.stages_completed:
            self.stages_completed.append(stage)


__all__ = [
    "RunStage",
    "JobState",
    "TERMINAL_STATES",
    "IllegalTransition",
    "can_transition",
    "FailureReason",
    "Job",
    "RunStatus",
    "Run",
]
