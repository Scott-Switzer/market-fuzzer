from fastapi.testclient import TestClient

from app.api.app import app
from app.execution_arena import benchmark_matrix, challenge_overview, run_execution_challenge


def test_public_execution_challenge_runs_against_real_exchange() -> None:
    result = run_execution_challenge("aggressive_pov", "normal", 42)
    assert result["phase"] == "public_practice"
    assert result["metrics"]["completion_pct"] == 100.0
    assert result["replay"]["timeline"]
    assert result["evidence"]["mechanical_validity"] == "PASS"


def test_hidden_worlds_are_not_exposed_in_public_brief() -> None:
    brief = challenge_overview()
    assert all(item["released"] is False for item in brief["hidden_worlds"])
    client = TestClient(app)
    assert client.get("/api/execution-challenge/benchmarks").status_code == 403
    assert (
        client.get("/api/execution-challenge/benchmarks", headers={"X-Role": "instructor"}).status_code == 200
    )


def test_exchange_backed_benchmarks_reverse_public_and_hidden_ranks() -> None:
    matrix = benchmark_matrix(seeds=(42,))
    rows = {row["policy_id"]: row for row in matrix["rows"]}
    assert rows["aggressive_pov"]["public_rank"] == 1
    assert rows["guarded_pov"]["robustness_rank"] == 1
    assert rows["aggressive_pov"]["robustness_rank"] > 1
    assert matrix["provenance"]["strategy_submission_policy"] == "declarative_only"
