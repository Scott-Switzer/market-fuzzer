"""Idempotency + durable-result helpers for the canonical product path.

Phase 2.6 section 3 / 4 / 5: every long-running operation (approve / backtest /
campaign) reserves a durable idempotency record keyed by
``(scope, project_id, idempotency_key)``. A replay with the same request digest
returns the recorded resource and response; a replay with a DIFFERENT digest is a
conflict (HTTP 409). Result artifacts are written through ``ArtifactStore`` and
indexed in ``ArtifactIndexRow`` before the run/campaign is marked succeeded.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.persistence.models import (
    ArtifactIndexRow,
    IdempotencyRecordRow,
    JobRow,
    RunRow,
)
from app.strategy_lab.canonical.errors import IdempotencyConflictError


def canonical_digest(payload: Any) -> str:
    """SHA-256 of the canonical JSON of a request payload (for conflict checks)."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def reserve_idempotency(
    session: Session,
    *,
    scope: str,
    project_id: str,
    idempotency_key: str,
    request_payload: Any,
    resource_type: str,
    resource_id: str,
    response_json: dict,
) -> tuple[IdempotencyRecordRow, bool]:
    """Reserve (or return) an idempotency record. Concurrency-safe.

    Returns ``(record, created)`` where ``created`` is True only for the
    transaction that inserted the row. Callers MUST execute side effects only
    when ``created`` is True; otherwise they must replay from the stored
    resource/response (or report in-flight / prior-failure).

    * key absent -> insert this record (inside a SAVEPOINT), return (rec, True).
    * key present with the SAME digest -> return (existing, False).
    * key present with a DIFFERENT digest -> raise IdempotencyConflictError (409).
    * concurrent INSERT race -> the unique constraint fires; the losing
      transaction rolls back ONLY the savepoint, re-selects the winner's row,
      and applies the same digest rules (PostgreSQL-safe). Returns (winner, False).
    """
    digest = canonical_digest(request_payload)
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    def _select() -> IdempotencyRecordRow | None:
        return session.scalar(
            select(IdempotencyRecordRow).where(
                IdempotencyRecordRow.scope == scope,
                IdempotencyRecordRow.project_id == project_id,
                IdempotencyRecordRow.idempotency_key == idempotency_key,
            )
        )

    existing = _select()
    if existing is not None:
        if existing.request_digest == digest:
            return existing, False
        raise IdempotencyConflictError(
            f"idempotency key {idempotency_key!r} reused with a different {scope} request"
        )
    rec = IdempotencyRecordRow(
        id=_new_id(),
        scope=scope,
        project_id=project_id,
        idempotency_key=idempotency_key,
        request_digest=digest,
        resource_type=resource_type,
        resource_id=resource_id,
        response_json=response_json,
    )
    try:
        with session.begin_nested():
            session.add(rec)
            session.flush()
    except IntegrityError:
        # A concurrent request inserted the same (scope, project, key) first.
        # The savepoint rolled back only our insert; recover the winner's row.
        winner = _select()
        if winner is None:  # pragma: no cover - constraint fired, row must exist
            raise
        if winner.request_digest == digest:
            return winner, False
        raise IdempotencyConflictError(
            f"idempotency key {idempotency_key!r} reused with a different {scope} request"
        ) from None
    return rec, True


def get_idempotency(
    session: Session, *, scope: str, project_id: str, idempotency_key: str
) -> IdempotencyRecordRow | None:
    from sqlalchemy import select

    return session.scalar(
        select(IdempotencyRecordRow).where(
            IdempotencyRecordRow.scope == scope,
            IdempotencyRecordRow.project_id == project_id,
            IdempotencyRecordRow.idempotency_key == idempotency_key,
        )
    )


def set_idempotency_resource(
    session: Session,
    *,
    scope: str,
    project_id: str,
    idempotency_key: str,
    resource_type: str,
    resource_id: str,
) -> None:
    """Back-fill the resolved resource id onto the reservation (committed)."""
    ir = get_idempotency(session, scope=scope, project_id=project_id, idempotency_key=idempotency_key)
    if ir is not None and not ir.resource_id:
        ir.resource_id = resource_id
        ir.resource_type = resource_type
        session.flush()


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())


def create_pending_run(
    session: Session,
    *,
    project_id: str,
    strategy_id: str,
    strategy_version: int,
    strategy_hash: str,
    data_mode: str,
    limitations: list[str] | None = None,
) -> RunRow:
    """Create a Run in PENDING state (Phase 2.6 section 4 lifecycle)."""
    run = RunRow(
        id=_new_id(),
        project_id=project_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_hash=strategy_hash,
        data_mode=data_mode,
        status="created",
        limitations=limitations or [],
    )
    session.add(run)
    session.flush()
    return run


def job_idempotency_key(*, scope: str, project_id: str, idempotency_key: str) -> str:
    """Namespace job keys so the global jobs.idempotency_key unique constraint
    cannot collide across scopes or projects for the same client key."""
    return f"{scope}:{project_id}:{idempotency_key}"


def create_queued_job(
    session: Session,
    *,
    run_id: str,
    idempotency_key: str,
    stage: str,
    scope: str | None = None,
    project_id: str | None = None,
) -> JobRow:
    # Prefer scoped keys; fall back only for legacy call sites/tests.
    key = (
        job_idempotency_key(scope=scope, project_id=project_id, idempotency_key=idempotency_key)
        if scope and project_id
        else idempotency_key
    )
    job = JobRow(
        id=_new_id(),
        run_id=run_id,
        idempotency_key=key,
        stage=stage,
        state="queued",
        progress=0.0,
        inputs_frozen=False,
    )
    session.add(job)
    session.flush()
    return job


def write_artifact(
    session: Session,
    *,
    store,
    run_id: str,
    key: str,
    payload: Any,
    artifact_index: list[dict[str, Any]],
) -> dict[str, Any]:
    """Persist one JSON artifact and append its index entry to ``artifact_index``."""
    data_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default).encode(
        "utf-8"
    )
    sha = hashlib.sha256(data_bytes).hexdigest()
    store.put(key, data_bytes)
    entry = {
        "key": key,
        "sha256": sha,
        "size": len(data_bytes),
    }
    artifact_index.append(entry)
    session.add(
        ArtifactIndexRow(
            run_id=run_id,
            store="local_fs",
            artifact_key=key,
            sha256=sha,
            size=len(data_bytes),
        )
    )
    return entry


def read_artifact(store, *, run_id: str, key: str) -> Any:
    raw = store.get(key)
    if raw is None:
        raise FileNotFoundError(key)
    return json.loads(raw)


def get_default_store():
    """Return the process-wide ArtifactStore (filesystem, env-rooted)."""
    import os

    from app.evidence.artifact_store import FilesystemArtifactStore

    root = os.getenv("FENRIX_ARTIFACT_DIR", "artifacts")
    return FilesystemArtifactStore(root)


def verify_artifacts(session: Session, *, store, run_id: str) -> list[dict[str, Any]]:
    """Verify every indexed artifact for a run; raise ArtifactIntegrityError on
    miss/mismatch. A COMPLETED run with zero indexed artifacts is itself an
    integrity failure (Phase 2.6.1 gate 4/12) and is rejected."""
    from app.strategy_lab.canonical.errors import ArtifactIntegrityError

    rows = session.query(ArtifactIndexRow).filter(ArtifactIndexRow.run_id == run_id).all()
    if not rows:
        run = session.get(RunRow, run_id)
        if run is not None and run.status == "completed":
            raise ArtifactIntegrityError(f"run {run_id} is marked completed but has zero indexed artifacts")
    verified: list[dict[str, Any]] = []
    for row in rows:
        raw = store.get(row.artifact_key)
        if raw is None:
            raise ArtifactIntegrityError(f"missing artifact {row.artifact_key}")
        import hashlib as _h

        actual = _h.sha256(raw).hexdigest()
        if actual != row.sha256:
            raise ArtifactIntegrityError(
                f"hash mismatch for {row.artifact_key}: stored {row.sha256} actual {actual}"
            )
        verified.append(
            {
                "key": row.artifact_key,
                "sha256": row.sha256,
                "size": row.size,
            }
        )
    return verified


def cancel_job(session: Session, *, run_id: str) -> bool:
    """Durably cancel a run's in-flight job. Returns True if a job was cancelled.

    Uses the domain ``Job`` state machine so only a non-terminal job can be
    cancelled (RUNNING/QUEUED -> CANCELLED). A COMPLETED/FAILED/already-CANCELLED
    job is left untouched and returns False (idempotent, restart-safe).
    """
    from app.domain.run import Job, JobState, RunStage, RunStatus

    job = session.query(JobRow).filter(JobRow.run_id == run_id).first()
    if job is None:
        return False
    domain = Job(
        job_id=job.id,
        idempotency_key=job.idempotency_key,
        stage=RunStage(job.stage),
        state=JobState(job.state),
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        progress=job.progress,
        inputs_frozen=job.inputs_frozen,
        result_ref=job.result_ref,
    )
    if domain.state in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED):
        return False
    domain.mark_cancelled()
    job.state = domain.state.value
    job.progress = domain.progress
    run = session.get(RunRow, run_id)
    if run is not None:
        run.status = RunStatus.FAILED.value  # cancelled run is not completable
    session.flush()
    session.commit()
    return True


def reclaim_stale_jobs(session: Session, *, stale_after_seconds: int = 3600) -> int:
    """Recovery-sweeper step: resolve jobs left RUNNING past ``stale_after_seconds``
    (e.g. after a worker/API crash). Re-queues for retry while attempts remain,
    otherwise marks FAILED in its own compensating transaction.

    Returns the number of jobs resolved. Idempotent and safe to run on every
    restart.
    """
    from datetime import timedelta

    from app.domain.run import Job, JobState, RunStage, RunStatus

    cutoff = now() - timedelta(seconds=stale_after_seconds)
    stale = (
        session.query(JobRow)
        .filter(JobRow.state == JobState.RUNNING.value)
        .filter(JobRow.updated_at < cutoff)
        .all()
    )
    resolved = 0
    for job in stale:
        domain = Job(
            job_id=job.id,
            idempotency_key=job.idempotency_key,
            stage=RunStage(job.stage),
            state=JobState(job.state),
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            progress=job.progress,
            inputs_frozen=job.inputs_frozen,
            result_ref=job.result_ref,
        )
        if domain.attempts < domain.max_attempts:
            # This attempt was lost to a crash: record the failure, then re-queue
            # for the next attempt (FAILED -> QUEUED is the legal retry path).
            domain.transition(JobState.FAILED)
            domain.transition(JobState.QUEUED)
            job.state = domain.state.value
            job.attempts = job.attempts + 1
            job.progress = 0.0
            run = session.get(RunRow, job.run_id)
            if run is not None:
                run.status = RunStatus.RUNNING.value
        else:
            domain.transition(JobState.FAILED)  # exhausted retries
            job.state = domain.state.value
            job.progress = 1.0
            job.failure_json = {"code": "stale_job_reclaimed", "retryable": False}
            run = session.get(RunRow, job.run_id)
            if run is not None:
                run.status = RunStatus.FAILED.value
        session.flush()
        resolved += 1
    session.commit()
    return resolved


def now() -> datetime:
    return datetime.now(UTC)


def _json_default(o: Any) -> Any:
    from decimal import Decimal

    if isinstance(o, Decimal):
        return format(o.normalize(), "f")
    from datetime import date
    from datetime import datetime as _dt

    if isinstance(o, (_dt, date)):
        return o.isoformat()
    from numpy import ndarray

    if isinstance(o, ndarray):
        return o.tolist()
    raise TypeError(f"not JSON serializable: {type(o)!r}")


def _json_safe(o: Any) -> Any:
    """Recursively convert non-JSON-native values (Decimal, datetime, ndarray)
    into JSON-safe forms for storage in JSON columns."""
    from datetime import date
    from datetime import datetime as _dt
    from decimal import Decimal as _Decimal

    import numpy as np

    if isinstance(o, _Decimal):
        return format(o.normalize(), "f")
    if isinstance(o, (_dt, date)):
        return o.isoformat()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    return o


__all__ = [
    "canonical_digest",
    "reserve_idempotency",
    "get_idempotency",
    "set_idempotency_resource",
    "create_pending_run",
    "create_queued_job",
    "job_idempotency_key",
    "write_artifact",
    "read_artifact",
    "verify_artifacts",
    "now",
    "get_default_store",
    "_json_safe",
]
