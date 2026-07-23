from __future__ import annotations

from app.break_test.quant_validation import (
    builtin_strategy_ranges,
    sensitivity_analysis,
    worst_case_attribution,
)


def _prices() -> list[float]:
    return [100.0 + i * 0.1 for i in range(260)]


class TestQuantValidation:
    def test_sensitivity_sma(self) -> None:
        result = sensitivity_analysis(_prices(), "sma_crossover", {"fast": 20, "slow": 50})
        assert result["candidates_tested"] > 0
        assert result["best"] is not None
        assert "robustness_score" in result["best"]
        assert result["stability"]["parameter_stability"] in {"stable", "moderate", "unstable"}

    def test_worst_case_attribution(self) -> None:
        result = worst_case_attribution(_prices(), "sma_crossover", {"fast": 20, "slow": 50})
        assert result["overall_worst_regime"] is not None
        assert "regime_worst_cases" in result
        assert "historical_trade_sharpe" in result
        assert "turnover_by_regime_consistency" in result

    def test_builtin_strategy_ranges_sma(self) -> None:
        ranges = builtin_strategy_ranges("sma_crossover")
        assert "fast" in ranges
        assert "slow" in ranges
        assert ranges["fast"][0] < ranges["fast"][1]
        assert ranges["slow"][0] < ranges["slow"][1]


class TestQuantAPI:
    def test_sensitivity_api(self) -> None:
        from fastapi.testclient import TestClient

        from app.api.app import app

        response = TestClient(app).post(
            "/api/quant/sensitivity",
            json={"closes": _prices(), "strategy_type": "sma_crossover", "params": {"fast": 20, "slow": 50}},
        )
        assert response.status_code == 200
        data = response.json()
        assert "best" in data
        assert "stability" in data

    def test_worst_case_api(self) -> None:
        from fastapi.testclient import TestClient

        from app.api.app import app

        response = TestClient(app).post(
            "/api/quant/worst-case",
            json={
                "closes": _prices(),
                "strategy_type": "sma_crossover",
                "params": {"fast": 20, "slow": 50},
                "worlds_per_regime": 20,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "overall_worst_regime" in data
        assert "regime_worst_cases" in data
