"""Durable execution control (Phase 4 closure): cancellation + stale-job reclaim.

Proves the execution substrate survives restart and is controllable:
* cancel_job marks an in-flight job CANCELLED (terminal) and is idempotent on a
  terminal run -- safe to call on every restart.
* reclaim_stale_jobs resolves RUNNING jobs left past a threshold: re-queues
  while attempts remain, else marks FAILED -- the recovery-sweeper step.

SQLite, deterministic (uses the conftest ``db`` fixture for FK setup).
"""

from __future__ import annotations

from datetime import UTC

from app.persistence.models import (
    JobRow,
    Project,
    RunRow,
    Strategy,
    StrategyVersionRow,
)
from app.strategy_lab.canonical.durable import cancel_job, reclaim_stale_jobs


def _seed_chain(s):
    s.add(Project(id="p1", name="p1", owner="tester"))
    s.add(Strategy(id="s1", project_id="p1", name="s"))
    s.add(StrategyVersionRow(strategy_id="s1", version=1, canonical_hash="h", canonical_json="{}"))
    s.commit()


def _running_job(s, *, run_id="r1", attempts=0, max_attempts=3, age_seconds=0):
    """Create a PENDING run + RUNNING job; ``age_seconds`` back-dates updated_at
    so reclaim treats it as stale."""
    from datetime import datetime, timedelta

    run = RunRow(
        id=run_id,
        project_id="p1",
        strategy_id="s1",
        strategy_version=1,
        strategy_hash="h",
        data_mode="synthetic",
        status="running",
    )
    s.add(run)
    s.commit()  # persist run before the job FK references it
    job = JobRow(
        id="j1",
        run_id=run_id,
        idempotency_key="k1",
        stage="historical_backtest",
        state="running",
        attempts=attempts,
        max_attempts=max_attempts,
        progress=0.3,
    )
    if age_seconds:
        job.updated_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
    s.add(job)
    s.commit()
    return run, job


def test_cancel_job_marks_inflight_cancelled(db):
    s = db["factory"]()
    _seed_chain(s)
    _running_job(s)
    assert cancel_job(s, run_id="r1") is True
    s.close()

    s2 = db["factory"]()
    job = s2.query(JobRow).filter_by(run_id="r1").one()
    run = s2.query(RunRow).filter_by(id="r1").one()
    assert job.state == "cancelled"
    assert run.status == "failed"
    # Idempotent on terminal run.
    assert cancel_job(s2, run_id="r1") is False
    s2.close()


def test_reclaim_stale_job_requeues_when_attempts_remain(db):
    s = db["factory"]()
    _seed_chain(s)
    _running_job(s, attempts=1, max_attempts=3, age_seconds=7200)
    n = reclaim_stale_jobs(s, stale_after_seconds=3600)
    assert n == 1
    s.close()

    s2 = db["factory"]()
    job = s2.query(JobRow).filter_by(run_id="r1").one()
    run = s2.query(RunRow).filter_by(id="r1").one()
    assert job.state == "queued"  # requeued for retry
    assert run.status == "running"
    assert job.attempts == 2  # one retry attempt consumed
    s2.close()


def test_reclaim_stale_job_fails_when_retries_exhausted(db):
    s = db["factory"]()
    _seed_chain(s)
    _running_job(s, attempts=3, max_attempts=3, age_seconds=7200)
    n = reclaim_stale_jobs(s, stale_after_seconds=3600)
    assert n == 1
    s.close()

    s2 = db["factory"]()
    job = s2.query(JobRow).filter_by(run_id="r1").one()
    run = s2.query(RunRow).filter_by(id="r1").one()
    assert job.state == "failed"
    assert run.status == "failed"
    s2.close()


def test_reclaim_ignores_fresh_running_jobs(db):
    s = db["factory"]()
    _seed_chain(s)
    _running_job(s, attempts=0, max_attempts=3, age_seconds=0)  # just started
    n = reclaim_stale_jobs(s, stale_after_seconds=3600)
    assert n == 0  # not stale
    s.close()
