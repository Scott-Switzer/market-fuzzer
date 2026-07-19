import importlib

from fastapi.testclient import TestClient
from typer.testing import CliRunner

import app.product as product_module
from app.api.app import app
from app.cli import cli
from app.product import DEFAULT_PROPERTIES, STRATEGIES, export_fixture, run_search
from app.world import build_demo_world

api_module = importlib.import_module("app.api.app")


def test_health_schema_compile_validate_and_run():
    client = TestClient(app)
    assert client.get("/api/health").json()["engine"] == "compact_deterministic_pov_harness"
    ready = client.get("/api/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["database"] == "ok"
    assert ready.json()["artifact_store"] == "ok"
    assert "properties" in client.get("/api/schema").json()
    calibration = client.get("/api/calibration/demo")
    assert calibration.status_code == 200
    assert len(calibration.json()["calibration_pack"]["calibration"]["accepted_parameter_sets"]) == 3
    compiled = client.post(
        "/api/compile", json={"prompt": "thin liquidity and earnings shock", "seed": 3, "mode": "offline"}
    )
    assert compiled.status_code == 200
    body = compiled.json()
    assert body["validation"]["valid"]
    assert client.post("/api/validate", json=body["spec"]).json()["valid"]
    run = client.post("/api/run", json={"spec": body["spec"]})
    assert run.status_code == 200
    assert run.json()["trades"]


def test_cli_compile_and_validate(tmp_path):
    runner = CliRunner()
    compiled = runner.invoke(cli, ["compile", "--prompt", "normal market", "--seed", "6"])
    assert compiled.exit_code == 0
    path = tmp_path / "world.yaml"
    path.write_text(build_demo_world(6).to_yaml())
    validated = runner.invoke(cli, ["validate", str(path)])
    assert validated.exit_code == 0
    assert validated.stdout.startswith("valid ")


def test_regression_suite_executes_fixture_and_reports_invalid(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = tmp_path / "fixtures"
    store.mkdir()
    monkeypatch.setattr(api_module, "STORE", store)
    monkeypatch.setattr(product_module, "STORE", store)
    fragile = {
        "id": "pov_fragile",
        **STRATEGIES["pov_fragile"],
        "parameters": STRATEGIES["pov_fragile"]["defaults"],
    }
    failure = run_search(fragile, DEFAULT_PROPERTIES)
    export_fixture(failure, fragile, DEFAULT_PROPERTIES)
    (store / "invalid.yaml").write_text("schema_version: '0.0'\n")
    result = TestClient(app).post("/api/regression-suites/run")
    assert result.status_code == 200
    body = result.json()
    assert body["total"] == 2
    assert body["passing"] == 1
    assert body["invalid"] == 1
    assert body["status"] == "complete_with_invalid_fixtures"


def test_comparison_retests_corrected_strategy_on_exact_scenario():
    client = TestClient(app)
    failure = client.post(
        "/api/searches", json={"strategy_id": "pov_fragile", "properties": DEFAULT_PROPERTIES}
    ).json()
    comparison = client.post(
        "/api/comparisons",
        json={
            "strategy_id": "pov",
            "properties": DEFAULT_PROPERTIES,
            "scenario": {"failure_id": failure["id"]},
        },
    )
    assert comparison.status_code == 200
    body = comparison.json()
    assert body["same_scenario_and_seeds"] is True
    assert body["same_parent_order"] is True
    assert body["original"]["passed"] is False
    assert body["modified"]["passed"] is True
    assert body["original"]["scenario_hash"] == body["modified"]["scenario_hash"] == body["scenario_hash"]
    assert body["comparison_contract"]["original_strategy"]["id"] == "pov_fragile"
    assert body["comparison_contract"]["modified_strategy"]["id"] == "pov"
