import numpy as np
import pytest

from app.break_test.metrics import backtest_metrics, compute_equity_curve
from app.break_test.regimes import detect_regimes, run_forward_test
from app.break_test.reporting import build_failure_report
from app.break_test.service import get_available_strategies, run_break_test
from app.break_test.strategies import BUILTIN_STRATEGIES, compute_positions


def _demo_prices(length: int = 260) -> list[float]:
    rng = np.random.default_rng(42)
    return (100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, length)))).tolist()


class TestStrategies:
    def test_sma_crossover_positions(self) -> None:
        prices = np.array([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110])
        pos = compute_positions("sma_crossover", prices, fast=3, slow=5)
        assert len(pos) == len(prices)
        assert pos.dtype == float

    def test_breakout_positions(self) -> None:
        prices = np.array([100, 101, 102, 100, 98, 97, 105, 106, 107])
        pos = compute_positions("breakout", prices, entry_lookback=3, exit_lookback=2)
        assert len(pos) == len(prices)

    def test_rsi_reversion_positions(self) -> None:
        prices = np.array([100, 101, 102, 100, 98, 97, 95, 96, 97, 98, 99])
        pos = compute_positions("rsi_reversion", prices, period=5, oversold=30, overbought=70)
        assert len(pos) == len(prices)

    def test_unknown_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported strategy type"):
            compute_positions("unknown", np.array([100, 101, 102]))

    def test_builtin_strategies_are_available(self) -> None:
        assert "sma_crossover" in BUILTIN_STRATEGIES
        assert "breakout" in BUILTIN_STRATEGIES
        assert "rsi_reversion" in BUILTIN_STRATEGIES


class TestMetrics:
    def test_backtest_metrics_returns_expected_fields(self) -> None:
        prices = np.array(_demo_prices(100))
        pos = compute_positions("sma_crossover", prices, fast=5, slow=20)
        metrics = backtest_metrics(prices, pos)
        assert "total_return_pct" in metrics
        assert "max_drawdown_pct" in metrics
        assert "sharpe" in metrics
        assert "trades" in metrics
        assert "win_rate_pct" in metrics
        assert "turnover" in metrics

    def test_equity_curve_matches_final_return(self) -> None:
        prices = np.array(_demo_prices(100))
        pos = compute_positions("sma_crossover", prices, fast=5, slow=20)
        metrics = backtest_metrics(prices, pos)
        curve = compute_equity_curve(prices, pos)
        assert abs(curve[-1] - 1 - metrics["total_return_pct"] / 100) < 0.01


class TestRegimes:
    def test_detect_regimes_returns_analysis(self) -> None:
        prices = np.array(_demo_prices(260))
        analysis = detect_regimes(prices)
        assert "detected_drift" in analysis
        assert "detected_volatility" in analysis
        assert "regime" in analysis
        assert "high_vol_periods_pct" in analysis
        assert analysis["regime"] in (
          "low-vol / likely trend or range",
          "normal-vol / mixed",
          "elevated-vol / stress risk",
          "crisis-vol / tail risk",
      )

    def test_forward_test_returns_all_regimes(self) -> None:
        prices = np.array(_demo_prices(100))
        results = run_forward_test(prices, "sma_crossover", {"fast": 10, "slow": 30}, worlds_per_regime=5)
        assert len(results) == 4
        for r in results:
            assert "regime" in r
            assert "loss_rate_pct" in r
            assert "median_return_pct" in r
            assert "worst_drawdown_pct" in r


class TestService:
    def test_available_strategies_returns_all(self) -> None:
        strategies = get_available_strategies()
        assert len(strategies) >= 3

    def test_run_break_test_returns_full_report(self) -> None:
        prices = _demo_prices(120)
        result = run_break_test(prices, "sma_crossover", worlds_per_regime=10)
        assert "session_id" in result
        assert "strategy" in result
        assert "historical" in result
        assert "equity_curve" in result
        assert "regime_analysis" in result
        assert "forward_test" in result
        assert "failure_summary" in result
        assert "correction_suggestion" in result
        assert result["strategy"]["type"] == "sma_crossover"
        assert result["forward_test"]["total_worlds"] == 40

    def test_run_break_test_with_fix_and_retest(self) -> None:
        prices = _demo_prices(120)
        result = run_break_test(
            prices,
            "sma_crossover",
            params={"fast": 10, "slow": 30},
            fix_and_retest_params={"fast": 10, "slow": 50},
            worlds_per_regime=5,
        )
        assert result["corrected"] is not None
        assert result["corrected"]["strategy"]["parameters"]["slow"] == 50

    def test_run_break_test_rejects_short_prices(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            run_break_test([100.0] * 20, "sma_crossover")


class TestReporting:
    def test_failure_report_identifies_weakest(self) -> None:
        prices = np.array(_demo_prices(100))
        pos = compute_positions("sma_crossover", prices, fast=10, slow=30)
        historical = backtest_metrics(prices, pos)
        forward = run_forward_test(prices, "sma_crossover", {"fast": 10, "slow": 30}, worlds_per_regime=5)
        report = build_failure_report("sma_crossover", {"fast": 10, "slow": 30}, historical, forward)
        assert "Most vulnerable" in report["failure_summary"]
        assert "correction_suggestion" in report
        assert "alternatives" in report["correction_suggestion"]
