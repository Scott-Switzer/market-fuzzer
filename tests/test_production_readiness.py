from __future__ import annotations

import pathlib

import pytest

from app.break_test.metrics import backtest_metrics
from app.break_test.quant_validation import (
    builtin_strategy_ranges,
    sensitivity_analysis,
    worst_case_attribution,
)
from app.break_test.strategies import compute_positions
import numpy as np


def _prices() -> np.ndarray:
    return np.array([100.0 + i * 0.1 for i in range(260)], dtype=float)


class TestMetricsExtended:
    def test_expected_quant_metrics_present(self) -> None:
        px = _prices()
        pos = np.zeros_like(px)
        pos[20:80] = 1.0
        pos[80:140] = 0.0
        pos[140:] = 1.0
        metrics = backtest_metrics(px, pos)
        expected_keys = {
            "total_return_pct",
            "max_drawdown_pct",
            "max_dd_duration_days",
            "sharpe",
            "sortino",
            "calmar",
            "beta",
            "alpha",
            "profit_factor",
            "benchmark_total_return_pct",
            "benchmark_sharpe",
            "var_95_pct",
            "cvar_95_pct",
            "expectancy",
            "avg_trade_return_pct",
        }
        missing = sorted(expected_keys - set(metrics.keys()))
        assert not missing, f"missing metrics: {missing}"

    def test_drawdown_duration_non_negative(self) -> None:
        px = _prices()
        pos = np.ones_like(px)
        metrics = backtest_metrics(px, pos)
        assert metrics["max_dd_duration_days"] >= 0

    def test_benchmark_total_return(self) -> None:
        px = _prices()
        pos = np.ones_like(px)
        metrics = backtest_metrics(px, pos)
        bench = float((px[-1] - px[0]) / px[0] * 100)
        assert abs(metrics["benchmark_total_return_pct"] - bench) < 1e-6


class TestStrategiesMetadata:
    def test_param_ranges_available_for_builtins(self) -> None:
        for strategy_type in ["sma_crossover", "breakout", "rsi_reversion"]:
            ranges = builtin_strategy_ranges(strategy_type)
            assert ranges, f"missing ranges for {strategy_type}"
            low, high = next(iter(ranges.values()))
            assert low < high

    def test_sensitivity_output_shape_sma(self) -> None:
        result = sensitivity_analysis(_prices().tolist(), "sma_crossover", {"fast": 20, "slow": 50})
        assert result["candidates_tested"] >= 1
        assert result["stability"]["parameter_stability"] in {"stable", "moderate", "unstable"}

    def test_worst_case_regime_count(self) -> None:
        result = worst_case_attribution(_prices().tolist(), "sma_crossover", {"fast": 20, "slow": 50}, worlds_per_regime=20)
        assert len(result["regime_worst_cases"]) == 4


class TestBreakTestAPI:
    def test_strategies_endpoint(self) -> None:
        from fastapi.testclient import TestClient
        from app.api.app import app
        response = TestClient(app).get("/api/break-test/strategies")
        assert response.status_code == 200
        body = response.json()
        assert "sma_crossover" in body
        assert body["sma_crossover"]["param_ranges"]["fast"]["min"] < body["sma_crossover"]["param_ranges"]["fast"]["max"]

    def test_run_requires_minimum_prices(self) -> None:
        from fastapi.testclient import TestClient
        from app.api.app import app
        response = TestClient(app).post("/api/break-test/run", json={"closes": [100, 101], "strategy_type": "sma_crossover"})
        assert response.status_code == 422

    def test_session_roundtrip(self) -> None:
        from fastapi.testclient import TestClient
        from app.api.app import app
        run = TestClient(app).post("/api/break-test/run", json={"closes": _prices().tolist(), "strategy_type": "sma_crossover", "params": {"fast": 20, "slow": 50}})
        assert run.status_code == 200
        session_id = run.json()["session_id"]
        session = TestClient(app).get(f"/api/break-test/session/{session_id}")
        assert session.status_code == 200
        body = session.json()
        assert body["strategy"]["type"] == "sma_crossover"
        assert "failure_analysis" in body
        assert sorted(body["historical"].keys()) == sorted([
            "total_return_pct","max_drawdown_pct","max_dd_duration_days","sharpe","sortino","calmar",
            "trades","turnover","win_rate_pct","profit_factor","benchmark_total_return_pct",
            "benchmark_sharpe","alpha","beta","avg_trade_return_pct","expectancy","var_95_pct","cvar_95_pct"
        ])
