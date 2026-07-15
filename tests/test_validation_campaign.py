import json

from app.experiments import run_validation_campaign
from app.experiments.artifacts import VALIDATION_REQUIRED_FILES, sha256
from app.world import build_demo_world


def test_calibrate_intervene_validate_release_campaign(tmp_path):
    result = run_validation_campaign(build_demo_world(23), tmp_path, "quick")
    raw_runs = result.intervention_summary["raw_runs"]
    assert len(raw_runs) == 4 * 8 * 3
    assert len({row["calibration_parameter_set_id"] for row in raw_runs}) == 3
    assert len({row["seed"] for row in raw_runs}) == 8
    assert {row["participation_rate"] for row in raw_runs} == {0.02, 0.05, 0.10, 0.20}
    assert result.simulator_validation_report["use_case"]["use_case"] == "execution_stress_testing"
    assert "Estimate production execution capacity." in result.simulator_validation_report["blocked_claims"]
    assert result.synthetic_release_validation_report["membership_inference"] == "NOT_APPLICABLE"
    for filename in VALIDATION_REQUIRED_FILES:
        assert (result.artifact_dir / filename).exists(), filename
    manifest = json.loads((result.artifact_dir / "manifest.json").read_text())
    for filename, expected in manifest["artifact_hashes"].items():
        assert sha256(result.artifact_dir / filename) == expected
    calibration = (result.artifact_dir / "calibration_pack.json").read_text()
    assert '"raw_rows_retained": false' in calibration
    assert str(tmp_path) not in calibration
    groups = {}
    for row in raw_runs:
        key = (row["calibration_parameter_set_id"], row["seed"])
        groups.setdefault(key, set()).add(row["participation_rate"])
    assert all(rates == {0.02, 0.05, 0.10, 0.20} for rates in groups.values())
