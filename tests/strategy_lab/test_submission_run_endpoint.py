"""Regression test for the /api/strategy-lab/submission/run endpoint.

The endpoint previously 500'd with KeyError: 'failures' because it indexed
run.stress["failures"], a key the stress result does not expose (it uses
"confirmed_failures"). This test pins the contract so the live pipeline's
final stage (minimize + evidence) cannot silently regress.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_submission_run_offline_returns_full_payload():
    client = TestClient(app)
    resp = client.post(
        "/api/strategy-lab/submission/run",
        json={"mode": "synthetic_fixture", "budget": 4},
    )
    assert resp.status_code == 200, resp.text[:500]
    d = resp.json()
    # hash invariant must flow through every stage
    assert d["strategy_hash"]
    assert d["approval"]["strategy_id"] == d["strategy_hash"]
    assert d["backtest"]["metrics"]["sharpe"] != 0.0
    # stress block: keyed by confirmed_failures, not "failures"
    stress = d["stress"]
    assert "evaluated" in stress
    assert "failure_count" in stress
    assert "failed_mechanisms" in stress
    assert isinstance(stress["failed_mechanisms"], list)
    # evidence package must be present and hash-stamped
    assert d["evidence"]["manifest"]["git_sha"]
    assert d["minimized"] is not None or d["stress"]["failure_count"] == 0
