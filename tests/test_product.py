import json

from typer.testing import CliRunner

from app.cli import cli
from app.product import DEFAULT_PROPERTIES, STRATEGIES, evaluate, export_fixture, run_search


def test_fragile_pov_baseline_passes_and_search_finds_reproducible_failure():
    strategy = {
        "id": "pov_fragile",
        **STRATEGIES["pov_fragile"],
        "parameters": STRATEGIES["pov_fragile"]["defaults"],
    }
    baseline = evaluate(
        strategy,
        {"liquidity": 1, "volatility": 1, "latency_ms": 10, "forced_seller": 0, "spread": 1},
        DEFAULT_PROPERTIES,
        42,
    )
    assert baseline["passed"]
    failure = run_search(strategy, DEFAULT_PROPERTIES)
    assert failure["found"]
    assert failure["reproduction"]["seeds_failed"] >= 2
    assert failure["evaluation_evidence"]["scope"] == "adaptive_diagnostic"
    assert "not independently selected primary" in failure["evaluation_evidence"]["claim_boundary"].lower()


def test_fragile_failure_is_targeted_minimized_and_corrected_strategy_passes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fragile = {
        "id": "pov_fragile",
        **STRATEGIES["pov_fragile"],
        "parameters": STRATEGIES["pov_fragile"]["defaults"],
    }
    corrected = {"id": "pov", **STRATEGIES["pov"], "parameters": STRATEGIES["pov"]["defaults"]}
    failure = run_search(fragile, DEFAULT_PROPERTIES)
    seeds = failure["reproduction"]["seeds_tested"]
    assert failure["violated_property"]["id"] == "participation"
    assert failure["severity"]["score"] <= 0.1
    assert failure["scenario_hash"] == failure["runs"][0]["scenario_hash"]
    assert failure["passing_neighbor_scenario_hash"] == failure["passing_neighbor_runs"][0]["scenario_hash"]
    assert all(
        "severity_before" in step and "severity_after" in step for step in failure["minimization_trace"]
    )
    assert all(
        not evaluate(fragile, failure["minimized"], DEFAULT_PROPERTIES, seed)["passed"] for seed in seeds
    )
    assert all(run["passed"] for run in failure["passing_neighbor_runs"])
    assert all(
        evaluate(corrected, failure["minimized"], DEFAULT_PROPERTIES, seed)["passed"] for seed in seeds
    )
    exported = export_fixture(failure, fragile, DEFAULT_PROPERTIES)
    assert exported["fixture"]["strategy"]["id"] == "pov_fragile"
    assert (tmp_path / exported["yaml"]).exists()

    runner = CliRunner()
    yaml_result = runner.invoke(cli, ["test", exported["yaml"]])
    json_result = runner.invoke(cli, ["test", exported["json"]])
    assert yaml_result.exit_code == 0, yaml_result.stdout
    assert json_result.exit_code == 0, json_result.stdout
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    (suite_dir / "case.yaml").write_text((tmp_path / exported["yaml"]).read_text())
    (suite_dir / "case.json").write_text((tmp_path / exported["json"]).read_text())
    directory_result = runner.invoke(cli, ["test", str(suite_dir)])
    assert directory_result.exit_code == 0, directory_result.stdout

    mismatch = json.loads((tmp_path / exported["json"]).read_text())
    mismatch["expected"]["result"] = "pass"
    mismatch_path = tmp_path / "mismatch.json"
    mismatch_path.write_text(json.dumps(mismatch))
    assert runner.invoke(cli, ["test", str(mismatch_path)]).exit_code == 1


def test_search_requires_enabled_target_property():
    fragile = {
        "id": "pov_fragile",
        **STRATEGIES["pov_fragile"],
        "parameters": STRATEGIES["pov_fragile"]["defaults"],
    }
    properties = [p for p in DEFAULT_PROPERTIES if p["id"] != "participation"]
    result = run_search(fragile, properties)
    assert result["found"] is False
    assert "participation" in result["message"]
