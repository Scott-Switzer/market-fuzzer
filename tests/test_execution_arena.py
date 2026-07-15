from fastapi.testclient import TestClient

from app.api.app import app
from app.execution_arena import (
    ExecutionPolicySubmission,
    benchmark_matrix,
    challenge_overview,
    run_execution_challenge,
)


def test_public_execution_challenge_runs_against_real_exchange() -> None:
    result = run_execution_challenge("aggressive_pov", "normal", 42)
    assert result["phase"] == "public_practice"
    assert result["metrics"]["completion_pct"] == 100.0
    assert result["replay"]["timeline"]
    assert result["evidence"]["mechanical_validity"] == "PASS"


def test_hidden_worlds_are_not_exposed_in_public_brief(monkeypatch) -> None:
    brief = challenge_overview()
    assert "liquidity" not in str(brief["hidden_worlds"]).lower()
    client = TestClient(app)
    assert client.get("/api/execution-challenge/benchmarks").status_code == 403
    assert (
        client.get("/api/execution-challenge/benchmarks", headers={"X-Role": "instructor"}).status_code == 403
    )
    monkeypatch.setenv("ARENA_TEST_AUTH", "1")
    assert (
        client.get("/api/execution-challenge/benchmarks", headers={"X-Test-Role": "instructor"}).status_code
        == 200
    )


def test_student_cannot_request_hidden_world_from_public_practice() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/arena/execution/challenges/trade-the-shock/practice",
        json={"policy_id": "aggressive_pov", "world_variant": "liquidity_withdrawal", "seed": 42},
    )
    assert response.status_code == 422
    assert "liquidity_withdrawal" not in client.get("/api/arena/execution/challenges/trade-the-shock").text
    assert client.get("/api/arena/execution/challenges/trade-the-shock/leaderboard/hidden").status_code == 403


def test_declarative_submission_rejects_extra_fields_and_accepts_valid_policy() -> None:
    valid = {
        "schema_version": "1.0",
        "strategy_type": "adaptive_pov",
        "target_participation": 0.08,
        "max_participation": 0.10,
        "max_spread_bps": 12,
        "urgency_curve": "adaptive",
        "feed_latency_tolerance_ms": 10,
        "cancel_after_ms": 100,
        "completion_buffer_steps": 3,
        "pause_during_halt": True,
        "pause_above_spread_limit": True,
        "include_pending_in_budget": True,
        "rationale": "I cap participation and pause when the public book is too wide to avoid chasing noise.",
    }
    assert ExecutionPolicySubmission.model_validate(valid).strategy_type == "adaptive_pov"
    client = TestClient(app)
    assert (
        client.post(
            "/api/arena/execution/challenges/trade-the-shock/submissions", json={"policy": valid}
        ).status_code
        == 200
    )
    invalid = {**valid, "unexpected": True}
    assert (
        client.post(
            "/api/arena/execution/challenges/trade-the-shock/submissions", json={"policy": invalid}
        ).status_code
        == 422
    )


def test_exchange_backed_benchmarks_reverse_public_and_hidden_ranks() -> None:
    matrix = benchmark_matrix(seeds=(42,))
    rows = {row["policy_id"]: row for row in matrix["rows"]}
    assert rows["aggressive_pov"]["public_rank"] == 1
    assert rows["guarded_pov"]["robustness_rank"] == 1
    assert rows["aggressive_pov"]["robustness_rank"] > 1
    assert matrix["provenance"]["strategy_submission_policy"] == "declarative_only"
