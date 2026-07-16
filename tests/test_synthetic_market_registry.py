from fastapi.testclient import TestClient

from app.api.app import app


def test_enterprise_world_registry_persists_versioned_manifest(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARENA_DB_PATH", str(tmp_path / "registry.sqlite3"))
    monkeypatch.setenv("ARENA_TEST_AUTH", "1")
    client = TestClient(app)
    response = client.post(
        "/api/enterprise/worlds",
        json={
            "name": "US equities intraday baseline",
            "description": "A reproducible baseline world for execution resilience experiments.",
            "asset_universe": ["NOVA", "ORBIT"],
            "agent_ecology": ["market_maker", "background_flow", "execution_agent"],
            "intended_use": "execution_stress_testing",
        },
    )
    assert response.status_code == 200
    world = response.json()
    assert world["version"] == 1
    assert world["manifest"]["schema_version"] == "1.0"
    assert len(world["manifest_hash"]) == 64
    assert client.get(f"/api/enterprise/worlds/{world['world_id']}").json()["world_id"] == world["world_id"]


def test_scenario_pack_requires_registered_world_and_preserves_manifest(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARENA_DB_PATH", str(tmp_path / "registry.sqlite3"))
    monkeypatch.setenv("ARENA_TEST_AUTH", "1")
    client = TestClient(app)
    missing = client.post(
        "/api/enterprise/scenario-packs",
        json={
            "name": "Liquidity withdrawal pack",
            "description": "Stress displayed depth and execution discipline under pressure.",
            "base_world_id": "world-does-not-exist",
            "intended_question": "Does the strategy remain controlled when displayed liquidity contracts?",
            "interventions": [
                {
                    "intervention_type": "liquidity_withdrawal",
                    "severity": "high",
                    "start_step": 20,
                    "duration_steps": 10,
                    "rationale": "Test behavior when displayed liquidity withdraws quickly.",
                }
            ],
        },
    )
    assert missing.status_code == 404
