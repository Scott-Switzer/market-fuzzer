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
