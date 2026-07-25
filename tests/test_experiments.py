import json

import pandas as pd

from app.experiments import run_batch
from app.experiments.artifacts import REQUIRED_FILES, sha256
from app.world import build_demo_world


def test_batch_generates_self_contained_artifacts(tmp_path):
    result = run_batch(build_demo_world(5), tmp_path, quick=True)
    assert len(result.runs) == 24
    assert {row["scenario"] for row in result.runs} == {
        "normal",
        "liquidity_withdrawal",
        "earnings_shock",
        "crowded_unwind",
    }
    assert {row["seed"] for row in result.runs} == {5, 6}
    for filename in REQUIRED_FILES:
        assert (result.artifact_dir / filename).exists(), filename
    assert not pd.read_parquet(result.artifact_dir / "trades.parquet").empty
    manifest = json.loads((result.artifact_dir / "manifest.json").read_text())
    for filename, expected in manifest["artifact_hashes"].items():
        assert sha256(result.artifact_dir / filename) == expected


def test_realism_and_failure_surface_are_component_level(tmp_path):
    result = run_batch(build_demo_world(8), tmp_path, quick=True)
    assert result.realism_report["classification"] == "component diagnostics with invariant harness"
    assert all(
        row["status"] in {"Pass", "Partial", "Fail", "Not evaluated"}
        for row in result.realism_report["metrics"]
    )
    assert result.failure_surface["cells"]
    assert result.failure_surface["best"]
    assert result.failure_surface["worst"]
