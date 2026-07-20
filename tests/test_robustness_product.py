import numpy as np

from app.robustness_product import evaluate_sma_robustness


def test_sma_report_contains_historical_forward_and_failure_evidence() -> None:
    rng = np.random.default_rng(7)
    closes = (100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, 260)))).tolist()
    report = evaluate_sma_robustness(closes, fast=10, slow=30, worlds_per_regime=5)
    assert report["strategy"]["name"] == "SMA crossover"
    assert report["synthetic_forward_test"]["worlds"] == 20
    assert len(report["synthetic_forward_test"]["regimes"]) == 4
    assert "most vulnerable" in report["failure_summary"]
    assert "win_rate_pct" in report["historical_backtest"]
    assert report["suggested_test"]["slow_window"] > 30


def test_sma_rejects_too_little_history() -> None:
    try:
        evaluate_sma_robustness([100.0] * 20)
    except ValueError as error:
        assert "at least 80" in str(error)
    else:
        raise AssertionError("short history was accepted")


def test_distinct_builtin_strategies_run_unchanged_across_worlds() -> None:
    prices = (100 * np.exp(np.cumsum(np.random.default_rng(9).normal(0, 0.01, 180)))).tolist()
    for kind in ("breakout", "rsi_reversion"):
        report = evaluate_sma_robustness(prices, fast=10, slow=30, worlds_per_regime=5, strategy_type=kind)
        assert report["strategy"]["type"] == kind
        assert report["synthetic_forward_test"]["worlds"] == 20
