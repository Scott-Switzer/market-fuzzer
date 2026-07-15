from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.api.app import app
from app.cli import cli
from app.world import build_demo_world


def test_health_schema_compile_validate_and_run():
    client = TestClient(app)
    assert client.get("/api/health").json()["engine"] == "compact_deterministic_pov_harness"
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
