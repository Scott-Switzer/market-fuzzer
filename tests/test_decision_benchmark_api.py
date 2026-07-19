from fastapi.testclient import TestClient

from app.api.app import app


def test_decision_benchmark_api_exposes_hashed_decision_evidence() -> None:
    response = TestClient(app).get("/api/enterprise/decision-benchmark?seeds=41,42")

    assert response.status_code == 200
    body = response.json()
    assert body["decision_changed"] is True
    assert body["artifact_hash"]
    assert body["public_winner"]["policy_id"] == "aggressive_pov"


def test_decision_benchmark_api_rejects_invalid_seed_input() -> None:
    response = TestClient(app).get("/api/enterprise/decision-benchmark?seeds=41,nope")

    assert response.status_code == 422
