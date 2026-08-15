"""Durable execution recovery (Phase 4).

Proves a long-running operation survives an API/worker restart -- the actual
"durable execution" property (the in-process Job state machine is covered
separately by ``test_run_lifecycle.py``).

Two complementary proofs:

* End-to-end through the real API: a completed backtest, replayed from a FRESH
  app/session (``reopen`` -- same DB file, new process) returns the SAME run id
  and verbatim response. No re-execution.
* At the idempotency + run/job substrate: a reserved-but-incomplete run raises
  IdempotencyInFlightError on replay (the recovery sweeper owns completion, a
  second worker must NOT double-execute), and a FAILED run/job persisted in its
  compensating transaction is reported as a prior failure after restart.

All on SQLite via the shared conftest fixtures (deterministic, no server).
"""

from __future__ import annotations

import pytest

from app.strategy_lab.canonical.backtest_service import _replay_or_reject_backtest
from app.strategy_lab.canonical.errors import (
    IdempotencyInFlightError,
    PriorAttemptFailedError,
)


def _compile(client, text):
    r = client.post("/api/strategy-lab/v2/compile", json={"description": text})
    assert r.status_code == 200, r.text
    return r.json()


def _project(client, name="WS"):
    r = client.post("/api/strategy-lab/v2/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _approve(client, project_id, spec_draft, canonical_hash):
    return client.post(
        "/api/strategy-lab/v2/approve",
        json={
            "project_id": project_id,
            "spec_draft": spec_draft,
            "actor": "user",
            "idempotency_key": "appr-" + canonical_hash,
        },
    )


def _backtest(client, a, universe):
    return client.post(
        "/api/strategy-lab/v2/backtests",
        json={
            "strategy_id": a["strategy_id"],
            "strategy_version": a["strategy_version"],
            "expected_canonical_hash": a["canonical_hash"],
            "data_source": {"source": "demo_fixture", "universe": universe, "benchmark": None},
            "initial_capital": 1000000,
            "idempotency_key": "bt-" + a["canonical_hash"],
        },
    )


def test_completed_backtest_replays_verbatim_after_restart(client, reopen):
    """A finished run, replayed from a fresh app (restart), returns the SAME run
    and verbatim response -- durable execution, no re-execution."""
    thesis = "Allocate 60% to SPY and 40% to AGG and rebalance monthly."
    c = _compile(client, thesis)
    pid = _project(client)
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    bt = _backtest(client, a, ["SPY", "AGG"])
    assert bt.status_code == 200, bt.text
    first = bt.json()
    run_id = first["run_id"]

    # Simulate an application/worker restart against the same DB.
    client2 = reopen()
    bt2 = _backtest(client2, a, ["SPY", "AGG"])
    assert bt2.status_code == 200, bt2.text
    second = bt2.json()

    assert second["run_id"] == run_id  # replay, not a second execution
    assert second == first  # verbatim response (artifact-integrity verified)


def _seed_chain(s):
    """Create the FK chain a RunRow requires (Project -> Strategy -> Version)."""
    from app.persistence.models import Project, Strategy, StrategyVersionRow

    s.add(Project(id="p1", name="p1", owner="tester"))
    s.add(Strategy(id="s1", project_id="p1", name="s"))
    s.add(StrategyVersionRow(strategy_id="s1", version=1, canonical_hash="h", canonical_json="{}"))
    s.commit()


def test_in_flight_reservation_coalesces_across_sessions(db):
    """A reserved-but-incomplete run raises IdempotencyInFlightError on replay
    (recovery sweeper owns completion); the PENDING rows survive the session
    boundary."""
    from app.persistence.models import JobRow, RunRow
    from app.strategy_lab.canonical.durable import (
        create_pending_run,
        create_queued_job,
        reserve_idempotency,
        set_idempotency_resource,
    )

    s1 = db["factory"]()
    _seed_chain(s1)
    ir, created = reserve_idempotency(
        s1,
        scope="backtest",
        project_id="p1",
        idempotency_key="k-inflight",
        request_payload={"strategy_id": "s1", "strategy_version": 1},
        resource_type="run",
        resource_id="",
        response_json={},
    )
    assert created is True
    run = create_pending_run(
        s1, project_id="p1", strategy_id="s1", strategy_version=1, strategy_hash="h", data_mode="synthetic"
    )
    create_queued_job(
        s1,
        run_id=run.id,
        idempotency_key="k-inflight",
        stage="historical_backtest",
        scope="backtest",
        project_id="p1",
    )
    set_idempotency_resource(
        s1,
        scope="backtest",
        project_id="p1",
        idempotency_key="k-inflight",
        resource_type="run",
        resource_id=run.id,
    )  # production durability checkpoint
    s1.commit()
    s1.close()

    # Fresh session (restart).
    s2 = db["factory"]()
    ir2, created2 = reserve_idempotency(
        s2,
        scope="backtest",
        project_id="p1",
        idempotency_key="k-inflight",
        request_payload={"strategy_id": "s1", "strategy_version": 1},
        resource_type="run",
        resource_id="",
        response_json={},
    )
    assert created2 is False
    with pytest.raises(IdempotencyInFlightError):
        _replay_or_reject_backtest(s2, ir2, "k-inflight")

    run = s2.query(RunRow).filter_by(project_id="p1").one()
    job = s2.query(JobRow).filter_by(run_id=run.id).one()
    assert run.status == "created"  # still pending after restart
    assert job.state == "queued"
    s2.close()


def test_failed_run_reported_as_prior_failure_after_restart(db):
    """A FAILED run/job persisted in the compensating transaction is reported as
    a prior failure (not a silent re-run) when replayed after a restart."""
    from app.strategy_lab.canonical.backtest_service import _persist_failed_lifecycle
    from app.strategy_lab.canonical.durable import (
        create_pending_run,
        create_queued_job,
        reserve_idempotency,
        set_idempotency_resource,
    )

    s1 = db["factory"]()
    _seed_chain(s1)
    ir, created = reserve_idempotency(
        s1,
        scope="backtest",
        project_id="p1",
        idempotency_key="k-fail",
        request_payload={"strategy_id": "s1", "strategy_version": 1},
        resource_type="run",
        resource_id="",
        response_json={},
    )
    run = create_pending_run(
        s1, project_id="p1", strategy_id="s1", strategy_version=1, strategy_hash="h", data_mode="synthetic"
    )
    create_queued_job(
        s1,
        run_id=run.id,
        idempotency_key="k-fail",
        stage="historical_backtest",
        scope="backtest",
        project_id="p1",
    )
    set_idempotency_resource(
        s1,
        scope="backtest",
        project_id="p1",
        idempotency_key="k-fail",
        resource_type="run",
        resource_id=run.id,
    )
    s1.commit()
    # Execution crashes: request txn rolls back, failure persisted in its own txn.
    s1.rollback()
    _persist_failed_lifecycle(s1, run.id, run.id, RuntimeError("boom"))
    s1.close()

    # Restart.
    s2 = db["factory"]()
    ir2, created2 = reserve_idempotency(
        s2,
        scope="backtest",
        project_id="p1",
        idempotency_key="k-fail",
        request_payload={"strategy_id": "s1", "strategy_version": 1},
        resource_type="run",
        resource_id="",
        response_json={},
    )
    assert created2 is False
    with pytest.raises(PriorAttemptFailedError):
        _replay_or_reject_backtest(s2, ir2, "k-fail")
    s2.close()
