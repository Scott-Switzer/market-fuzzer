import pytest
from fastapi.testclient import TestClient

from app.api.app import app
from app.calibration import build_demo_calibration_pack
from app.governance import build_enterprise_validation_report


def test_enterprise_world_registry_persists_versioned_manifest(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARENA_DB_PATH", str(tmp_path / "registry.sqlite3"))
    monkeypatch.setenv("ARENA_TEST_AUTH", "1")
    client = TestClient(app)
    response = client.post(
        "/api/enterprise/worlds",
        json={
            "name": "US equities intraday baseline",
            "description": "A reproducible baseline world for execution resilience experiments.",
            "seed": 77,
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
    assert world["manifest"]["seed"] == 77
    assert client.get(f"/api/enterprise/worlds/{world['world_id']}").json()["world_id"] == world["world_id"]


def test_real_calibration_pack_attaches_as_a_new_world_version(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARENA_DB_PATH", str(tmp_path / "registry.sqlite3"))
    monkeypatch.setenv("ARENA_TEST_AUTH", "1")
    client = TestClient(app)
    world = client.post(
        "/api/enterprise/worlds",
        json={
            "name": "Calibrated execution world",
            "description": "A world that records aggregate-only calibration provenance for stress testing.",
            "asset_universe": ["NOVA", "ORBIT", "VYNE"],
            "agent_ecology": ["market_maker", "fundamental", "execution_agent"],
        },
    ).json()
    pack = build_demo_calibration_pack(seed=8, rows=120).model_dump(mode="json")
    attached = client.post(f"/api/enterprise/worlds/{world['world_id']}/calibration", json=pack)
    assert attached.status_code == 200
    calibrated = attached.json()
    assert calibrated["version"] == 2
    assert calibrated["manifest"]["calibration_pack_id"] == pack["pack_id"]
    assert calibrated["manifest"]["calibration_run_id"].startswith("calibration-run-")
    assert (
        client.get(
            f"/api/enterprise/calibration-runs/{calibrated['manifest']['calibration_run_id']}"
        ).status_code
        == 200
    )
    assert client.get(f"/api/enterprise/calibration-packs/{pack['pack_id']}").status_code == 200


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


def test_scenario_pack_compiles_to_reproducible_protected_worlds(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARENA_DB_PATH", str(tmp_path / "registry.sqlite3"))
    monkeypatch.setenv("ARENA_TEST_AUTH", "1")
    client = TestClient(app)
    world = client.post(
        "/api/enterprise/worlds",
        json={
            "name": "Execution baseline",
            "description": "A reproducible baseline world for controlled stress experiments.",
            "seed": 77,
            "asset_universe": ["NOVA", "ORBIT", "VYNE"],
            "agent_ecology": ["market_maker", "fundamental", "execution_agent"],
        },
    ).json()
    pack = client.post(
        "/api/enterprise/scenario-packs",
        json={
            "name": "Liquidity resilience",
            "description": "A bounded stress pack for displayed-depth withdrawal.",
            "base_world_id": world["world_id"],
            "intended_question": "Does an execution strategy remain controlled when displayed liquidity contracts?",
            "interventions": [
                {
                    "intervention_type": "liquidity_withdrawal",
                    "severity": "high",
                    "start_step": 45,
                    "duration_steps": 10,
                    "rationale": "Measure execution behavior after displayed liquidity withdraws.",
                }
            ],
        },
    ).json()
    first = client.post(f"/api/enterprise/scenario-packs/{pack['scenario_pack_id']}/compile").json()
    second = client.post(f"/api/enterprise/scenario-packs/{pack['scenario_pack_id']}/compile").json()
    assert first["compile_hash"] == second["compile_hash"]
    assert len(first["protected_worlds"]) == 1
    assert first["seed"] == 77
    assert first["base_world_manifest_hash"] == world["manifest_hash"]
    assert first["protected_worlds"][0]["world"]["events"][0]["simulation_step"] == 45


def test_strategy_stress_lab_persists_experiment_result(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARENA_DB_PATH", str(tmp_path / "registry.sqlite3"))
    monkeypatch.setenv("ARENA_TEST_AUTH", "1")
    client = TestClient(app)
    world = client.post(
        "/api/enterprise/worlds",
        json={
            "name": "Stress lab baseline",
            "description": "A reproducible baseline world for strategy stress experiments.",
            "asset_universe": ["NOVA", "ORBIT", "VYNE"],
            "agent_ecology": ["market_maker", "fundamental", "execution_agent"],
        },
    ).json()
    pack = client.post(
        "/api/enterprise/scenario-packs",
        json={
            "name": "Latency stress",
            "description": "A bounded message-latency stress pack for execution strategy testing.",
            "base_world_id": world["world_id"],
            "intended_question": "Does the strategy remain controlled when order-entry latency rises?",
            "interventions": [
                {
                    "intervention_type": "latency_shock",
                    "severity": "moderate",
                    "start_step": 40,
                    "duration_steps": 10,
                    "rationale": "Measure behavior when market messages arrive with delay.",
                }
            ],
        },
    ).json()
    strategy = client.post(
        "/api/enterprise/strategies",
        json={
            "name": "Guarded POV adapter",
            "description": "A bounded guarded participation policy for execution stress testing.",
            "builtin_policy_id": "guarded_pov",
        },
    ).json()
    experiment = client.post(
        "/api/enterprise/experiments",
        json={
            "name": "Guarded POV latency stress",
            "strategy_ids": [strategy["strategy_id"]],
            "scenario_pack_id": pack["scenario_pack_id"],
            "seeds": [42],
        },
    )
    assert experiment.status_code == 200
    record = experiment.json()
    assert record["status"] == "completed"
    assert record["result"]["strategy_results"][0]["policy_id"] == "guarded_pov"
    job = client.post(
        "/api/enterprise/experiment-jobs",
        json={
            "name": "Resumable latency stress",
            "strategy_ids": [strategy["strategy_id"]],
            "scenario_pack_id": pack["scenario_pack_id"],
            "seeds": [43],
        },
    )
    assert job.status_code == 200
    assert job.json()["status"] == "queued"
    resumed = client.post(f"/api/enterprise/experiment-jobs/{job.json()['job_id']}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "completed"
    assert resumed.json()["progress"]["percent"] == 100
    assert resumed.json()["artifact"]["content_hash"]
    listed = client.get("/api/enterprise/experiments?limit=1&offset=0")
    assert listed.status_code == 200
    assert listed.json()["limit"] == 1
    assert listed.json()["experiments"][0]["experiment_id"] == record["experiment_id"]
    assert "result" not in listed.json()["experiments"][0]
    assert listed.json()["experiments"][0]["has_result"] is True
    validation = client.post(f"/api/enterprise/experiments/{record['experiment_id']}/validate")
    assert validation.status_code == 200
    report = validation.json()["report"]
    assert report["overall_verdict"] == "LIMITED"
    assert len(report["evidence_manifest"]["evidence_ids"]) == 1
    exported = client.get(f"/api/enterprise/experiments/{record['experiment_id']}/validation/export")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/json")


def test_validation_rejects_incomplete_experiment_and_preserves_provenance(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARENA_DB_PATH", str(tmp_path / "registry.sqlite3"))
    monkeypatch.setenv("ARENA_TEST_AUTH", "1")
    from app.execution_store import ArenaStore

    store = ArenaStore(tmp_path / "registry.sqlite3")
    store.save_stress_experiment(
        "experiment-incomplete",
        {"name": "Pending", "scenario_pack_id": "scenario-x", "strategy_ids": [], "seeds": [42]},
        "first-actor",
        {},
    )
    with pytest.raises(ValueError, match="no completed result"):
        build_enterprise_validation_report(
            {
                "experiment_id": "experiment-incomplete",
                "result": None,
            }
        )
    first = store.save_validation_report(
        "validation-experiment-incomplete", "experiment-incomplete", {"report_hash": "abc"}, "first-actor"
    )
    second = store.save_validation_report(
        "validation-experiment-incomplete", "experiment-incomplete", {"report_hash": "def"}, "second-actor"
    )
    assert first["created_by"] == second["created_by"] == "first-actor"
    assert second["report"]["report_hash"] == "abc"
    # The store writes completed results by design; exercise the public not-found contract too.
    client = TestClient(app)
    assert client.post("/api/enterprise/experiments/does-not-exist/validate").status_code == 404
