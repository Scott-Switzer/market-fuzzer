import pytest
from fastapi.testclient import TestClient

from app.api.app import app
from app.challenges.execution import ExecutionChallengeEngine
from app.execution_arena import (
    POLICIES,
    ExecutionPolicySubmission,
    _rank,
    _robustness_decomposition,
    benchmark_matrix,
    challenge_overview,
    policy_from_submission,
    policy_to_submission,
    run_execution_challenge,
    run_policy_submission,
)


def _valid_policy() -> dict:
    return {
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


@pytest.fixture(autouse=True)
def isolated_execution_database(tmp_path, monkeypatch):
    monkeypatch.setenv("ARENA_DB_PATH", str(tmp_path / "arena.sqlite3"))
    monkeypatch.setenv("ARENA_TEST_AUTH", "1")


def test_public_execution_challenge_runs_against_real_exchange() -> None:
    result = run_execution_challenge("aggressive_pov", "normal", 42)
    assert result["phase"] == "public_practice"
    assert result["metrics"]["completion_pct"] == 100.0
    assert result["replay"]["timeline"]
    assert result["evidence"]["mechanical_validity"] == "PASS"
    diagnostics = result["evidence"]["selected_synthetic_diagnostics"]
    assert diagnostics["accounting_all_steps"] is True
    assert diagnostics["scope"] == "selected_synthetic_market_diagnostics_not_real_market_calibration"
    assert set(diagnostics) >= {
        "spread_distribution_ticks",
        "displayed_depth_distribution_shares",
        "return_volatility_bps",
        "volume_clustering_lag1",
        "depth_spread_correlation",
        "price_response_to_signed_flow_correlation",
    }


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
        == 409
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


def test_public_practice_seed_is_server_fixed() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/arena/execution/challenges/trade-the-shock/practice",
        json={"policy_id": "aggressive_pov", "seed": 41},
    )
    assert response.status_code == 422
    assert (
        client.post(
            "/api/execution-challenge/run",
            json={"policy_id": "aggressive_pov", "seed": 41},
        ).status_code
        == 422
    )


def test_declarative_submission_rejects_extra_fields_and_accepts_valid_policy() -> None:
    valid = _valid_policy()
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
    with pytest.raises(ValueError, match="max_participation"):
        ExecutionPolicySubmission.model_validate({**valid, "max_participation": 0.05})
    with pytest.raises(ValueError):
        ExecutionPolicySubmission.model_validate({**valid, "target_participation": -0.01})


def test_rank_is_derived_from_score_not_policy_identity() -> None:
    rows = [
        {"policy_id": "name_that_sounds_guarded", "score": 10.0},
        {"policy_id": "name_that_sounds_aggressive", "score": 20.0},
    ]
    ranked = _rank(rows, "score")
    assert [row["policy_id"] for row in ranked] == [
        "name_that_sounds_aggressive",
        "name_that_sounds_guarded",
    ]
    renamed = _rank(
        [
            {"policy_id": "renamed_a", "score": 10.0},
            {"policy_id": "renamed_b", "score": 20.0},
        ],
        "score",
    )
    assert [row["score"] for row in renamed] == [20.0, 10.0]


def test_metric_change_affects_rank_through_declared_rubric_total() -> None:
    baseline = {
        "implementation_shortfall_bps": 40.0,
        "completion_pct": 100.0,
        "temporary_impact_bps": 50.0,
        "terminal_inventory_penalty": 0.0,
        "max_participation_pct": 10.0,
        "participation_limit_violations": 0,
        "time_weighted_remaining_parent_quantity": 2_000.0,
    }
    worse_shortfall = {**baseline, "implementation_shortfall_bps": 180.0}
    baseline_total = _robustness_decomposition([baseline])["total"]
    worse_total = _robustness_decomposition([worse_shortfall])["total"]
    assert baseline_total is not None and worse_total is not None
    assert baseline_total > worse_total
    ranked = _rank(
        [
            {"policy_id": "low_total", "robustness_score": worse_total, "unscored_note": 999},
            {"policy_id": "high_total", "robustness_score": baseline_total, "unscored_note": -999},
        ],
        "robustness_score",
    )
    assert [row["policy_id"] for row in ranked] == ["high_total", "low_total"]


def test_exchange_backed_benchmarks_reverse_public_and_hidden_ranks() -> None:
    matrix = benchmark_matrix(seeds=(42,))
    rows = {row["policy_id"]: row for row in matrix["rows"]}
    assert rows["aggressive_pov"]["public_rank"] == 1
    assert rows["guarded_pov"]["robustness_rank"] == 1
    assert rows["aggressive_pov"]["robustness_rank"] > 1
    assert matrix["provenance"]["strategy_submission_policy"] == "declarative_only"
    aggressive_public = rows["aggressive_pov"]["public_world_results"][0]
    guarded_public = rows["guarded_pov"]["public_world_results"][0]
    assert aggressive_public["environment_hash"] == guarded_public["environment_hash"]
    assert aggressive_public["policy_specification_hash"] != guarded_public["policy_specification_hash"]
    quality = matrix["provenance"]["quality"]
    assert quality["challenge_behavior"]["status"] == "PASS"
    checks = {check["id"]: check for check in quality["challenge_behavior"]["checks"]}
    assert set(checks) == {
        "liquidity_reduces_displayed_depth",
        "latency_increases_order_entry_delay",
        "crowding_increases_directional_sell_flow",
        "scheduled_event_activates",
        "all_worlds_preserve_accounting",
        "same_input_reproduces_identical_result_hash",
    }
    assert all(check["passed"] for check in checks.values())
    assert quality["selected_synthetic_diagnostics"]["status"] == "REPORTED_NOT_CALIBRATED"
    assert set(rows["aggressive_pov"]["released_intent_aggregates"]) == {
        "thin_liquidity",
        "message_latency",
        "directional_crowding",
        "scheduled_event",
    }
    aggressive_decomposition = rows["aggressive_pov"]["score_decomposition"]
    guarded_decomposition = rows["guarded_pov"]["score_decomposition"]
    assert aggressive_decomposition["order_hygiene"] is None
    assert guarded_decomposition["order_hygiene"] is None
    assert aggressive_decomposition != guarded_decomposition
    assert aggressive_decomposition["total"] == round(
        sum(value for key, value in aggressive_decomposition.items() if key != "total" and value is not None),
        3,
    )
    replayed = benchmark_matrix(seeds=(42,))
    assert replayed["provenance"]["matrix_hash"] == matrix["provenance"]["matrix_hash"]


def test_builtins_round_trip_through_public_policy_contract() -> None:
    for policy_id, builtin in POLICIES.items():
        public_policy = policy_to_submission(builtin)
        restored = policy_from_submission(public_policy, f"student-{policy_id}")
        assert restored.latency_ms == builtin.latency_ms
        assert restored.max_spread_bps == builtin.max_spread_bps
        assert restored.feed_latency_tolerance_ms == (
            builtin.feed_latency_tolerance_ms if builtin.feed_latency_tolerance_ms is not None else 10_000
        )
        builtin_run = run_execution_challenge(policy_id, "normal", 42)
        student_run = run_policy_submission(public_policy, f"student-{policy_id}", 42)
        assert student_run["metrics"] == builtin_run["metrics"]
        assert student_run["public_score"] == builtin_run["public_score"]


def test_custom_policy_uses_shared_challenge_engine_for_public_and_hidden_runs() -> None:
    engine = ExecutionChallengeEngine()
    policy = _valid_policy()
    assert engine.validate_submission(policy).valid is True
    public = engine.run_public(policy)
    hidden = engine.run_hidden(policy)
    custom = next(row for row in hidden["rows"] if row["policy_id"] == "engine-validation")
    assert custom["submission_id"] == "engine-validation"
    assert custom["public_score"] == public["public_score"]
    assert custom["world_results"]
    assert all(result["metrics"]["inventory_accounting_ties"] for result in custom["world_results"])
