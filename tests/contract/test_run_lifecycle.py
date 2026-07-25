"""Job state-machine and Run lifecycle tests (reset brief section 11)."""

from __future__ import annotations

import pytest

from app.domain.run import (
    FailureReason,
    IllegalTransition,
    Job,
    JobState,
    Run,
    RunStage,
    RunStatus,
    can_transition,
)


def _job(**kw) -> Job:
    kw.setdefault("idempotency_key", "k1")
    kw.setdefault("stage", RunStage.HISTORICAL_BACKTEST)
    return Job(**kw)


def test_default_state_is_queued():
    assert _job().state == JobState.QUEUED


def test_happy_path_transitions():
    j = _job()
    j.mark_running()
    assert j.state == JobState.RUNNING
    assert j.attempts == 1
    j.mark_succeeded(result_ref="run/abc")
    assert j.state == JobState.SUCCEEDED
    assert j.progress == 1.0
    assert j.result_ref == "run/abc"


def test_cannot_skip_running():
    j = _job()
    with pytest.raises(IllegalTransition):
        j.mark_succeeded()


def test_succeeded_is_terminal():
    j = _job()
    j.mark_running()
    j.mark_succeeded()
    with pytest.raises(IllegalTransition):
        j.transition(JobState.RUNNING)


def test_cancelled_is_terminal():
    j = _job()
    j.mark_cancelled()
    assert j.state == JobState.CANCELLED
    with pytest.raises(IllegalTransition):
        j.mark_running()


def test_can_cancel_while_running():
    j = _job()
    j.mark_running()
    j.mark_cancelled()
    assert j.state == JobState.CANCELLED


def test_retryable_failure_can_requeue():
    j = _job(max_attempts=3)
    j.mark_running()
    j.mark_failed(FailureReason(code="data_unavailable", message="yfinance timeout", retryable=True))
    assert j.can_retry()
    j.requeue_for_retry()
    assert j.state == JobState.QUEUED
    j.mark_running()
    assert j.attempts == 2


def test_deterministic_validation_error_not_retryable():
    j = _job()
    j.mark_running()
    j.mark_failed(FailureReason(code="validation_error", message="clause unresolved", retryable=False))
    assert not j.can_retry()
    with pytest.raises(IllegalTransition):
        j.requeue_for_retry()


def test_retry_budget_exhausted():
    j = _job(max_attempts=2)
    j.mark_running()  # attempt 1
    j.mark_failed(FailureReason(code="x", message="y", retryable=True))
    j.requeue_for_retry()
    j.mark_running()  # attempt 2
    j.mark_failed(FailureReason(code="x", message="y", retryable=True))
    assert not j.can_retry()  # attempts == max_attempts


def test_can_transition_table():
    assert can_transition(JobState.QUEUED, JobState.RUNNING)
    assert not can_transition(JobState.QUEUED, JobState.SUCCEEDED)
    assert not can_transition(JobState.SUCCEEDED, JobState.FAILED)


def test_run_stage_tracking_idempotent():
    r = Run(
        project_id="p1",
        strategy_id="s1",
        strategy_version=1,
        strategy_hash="h",
        data_mode="synthetic_fixture",
    )
    assert r.status == RunStatus.CREATED
    r.complete_stage(RunStage.COMPILE)
    r.complete_stage(RunStage.COMPILE)  # dedup
    assert r.stages_completed == [RunStage.COMPILE]
