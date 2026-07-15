from app.product import DEFAULT_PROPERTIES, STRATEGIES, evaluate, run_search


def test_fragile_pov_baseline_passes_and_search_finds_reproducible_failure():
    strategy = {"id": "pov_fragile", **STRATEGIES["pov_fragile"], "parameters": STRATEGIES["pov_fragile"]["defaults"]}
    baseline = evaluate(strategy, {"liquidity": 1, "volatility": 1, "latency_ms": 10, "forced_seller": 0, "spread": 1}, DEFAULT_PROPERTIES, 42)
    assert baseline["passed"]
    failure = run_search(strategy, DEFAULT_PROPERTIES)
    assert failure["found"]
    assert failure["reproduction"]["seeds_failed"] >= 2
