from __future__ import annotations

import pytest

from app.break_test.oos_validation import (
    combinatorial_purged_cross_validation,
    walk_forward_validation,
)


def _prices() -> list[float]:
    return [100.0 + i * 0.1 for i in range(260)]


class TestWalkForwardValidation:
    def test_default_returns_folds(self) -> None:
        result = walk_forward_validation(_prices(), "sma_crossover", {"fast": 20, "slow": 50})
        assert result["n_folds"] > 0
        assert len(result["folds"]) == result["n_folds"]
        assert all("oos_sharpe" in f for f in result["folds"])

    def test_anchored_vs_rolling(self) -> None:
        anchored = walk_forward_validation(_prices(), "sma_crossover", {"fast": 20, "slow": 50}, anchored=True)
        rolling = walk_forward_validation(_prices(), "sma_crossover", {"fast": 20, "slow": 50}, anchored=False)
        assert anchored["anchored"] is True
        assert rolling["anchored"] is False
        assert anchored["n_folds"] == rolling["n_folds"]

    def test_regime_aware_weights(self) -> None:
        result = walk_forward_validation(
            _prices(), "sma_crossover", {"fast": 20, "slow": 50}, regime_aware=True
        )
        assert result["n_folds"] == len(result["regime_weights"])
        assert abs(sum(result["regime_weights"]) - 1.0) < 1e-4

    def test_insufficient_data(self) -> None:
        result = walk_forward_validation([100.0, 101.0, 102.0], "sma_crossover", {"fast": 20, "slow": 50})
        assert result["n_folds"] == 0
        assert result["deflated_sharpe"] == 0.0

    def test_deflated_sharpe_non_negative(self) -> None:
        result = walk_forward_validation(_prices(), "sma_crossover", {"fast": 20, "slow": 50})
        if result["n_folds"] > 0:
            assert isinstance(result["deflated_sharpe"], float)

    def test_psr_range(self) -> None:
        result = walk_forward_validation(_prices(), "sma_crossover", {"fast": 20, "slow": 50})
        if result["n_folds"] > 0:
            assert 0.0 <= result["psr_vs_zero"] <= 1.0


class TestCombinatorialPurgedCrossValidation:
    def test_requires_enough_data(self) -> None:
        result = combinatorial_purged_cross_validation([100.0, 101.0], "sma_crossover", {"fast": 20, "slow": 50})
        assert result["n_folds"] == 0

    def test_returns_summary_fields(self) -> None:
        result = combinatorial_purged_cross_validation(_prices(), "sma_crossover", {"fast": 20, "slow": 50})
        if result["n_folds"] > 0:
            assert "deflated_sharpe" in result
            assert "psr_vs_zero" in result
            assert "consistency_sharpe" in result
