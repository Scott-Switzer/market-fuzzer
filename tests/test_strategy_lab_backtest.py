from __future__ import annotations

import json
import math
from unittest.mock import patch

import numpy as np
import pytest

from app.strategy_lab.historical.engine import (
    BacktestReport,
    HistoricalDataContract,
    backtest_report_to_dict,
    run_historical_backtest,
)
from app.strategy_lab.historical.metrics import HistoricalMetricsEngine


def _demo_prices(length: int = 120) -> list[float]:
    rng = np.random.default_rng(42)
    return (100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, length)))).tolist()


def _expensive_metric(report: BacktestReport) -> float:
    return float(report.metrics.get("total_return_pct", 0.0))


class TestHistoricalDataContract:
    def test_valid_contract_accepts_standard_fields(self) -> None:
        contract = HistoricalDataContract(freq="1d", start="2024-01-01", end="2024-12-31")
        assert contract.freq == "1d"
        assert contract.point_in_time_universe is True

    def test_sorted_default_fields(self) -> None:
        contract = HistoricalDataContract(freq="1d", start="2024-01-01", end="2024-12-31")
        assert list(contract.fields) == sorted(contract.fields)

    def test_unsupported_freq_raises(self) -> None:
        contract = HistoricalDataContract(freq="5m", start="2024-01-01", end="2024-12-31")
        with pytest.raises(ValueError, match="Unsupported freq"):
            run_historical_backtest(
                contract=contract,
                prices=[100.0, 101.0, 102.0],
                strategy_type="sma_crossover",
            )

    def test_non_point_in_time_raises(self) -> None:
        contract = HistoricalDataContract(
            freq="1d", start="2024-01-01", end="2024-12-31", point_in_time_universe=False
        )
        with pytest.raises(ValueError, match="Point-in-time"):
            run_historical_backtest(
                contract=contract,
                prices=[100.0, 101.0, 102.0],
                strategy_type="sma_crossover",
            )

    def test_to_dict_roundtrip(self) -> None:
        contract = HistoricalDataContract(
            freq="1h", start="2024-01-01", end="2024-01-02", fields=("open", "close"), assets=["A"]
        )
        data = contract.__dict__
        restored = HistoricalDataContract(**data)
        assert restored.freq == contract.freq
        assert restored.assets == ["A"]


class TestMetricsEngine:
    def test_compute_from_prices_and_positions(self) -> None:
        prices = _demo_prices(120)
        positions = [1.0 if i > 60 else 0.0 for i in range(len(prices))]
        result = HistoricalMetricsEngine.compute([], prices=prices, positions=positions)
        assert "sharpe" in result
        assert "sortino" in result
        assert "max_drawdown" in result
        assert result["status"] == "ok"

    def test_compute_from_equity_curve(self) -> None:
        curve = [1.0, 1.01, 1.02, 1.015, 1.025]
        result = HistoricalMetricsEngine.compute(curve)
        assert "sharpe" in result
        assert "max_drawdown" in result


class TestHistoricalEngine:
    def test_single_asset_sma_backtest_returns_report(self) -> None:
        contract = HistoricalDataContract(freq="1d", start="2024-01-01", end="2024-12-31")
        prices = _demo_prices(120)
        result = run_historical_backtest(contract=contract, prices=prices, strategy_type="sma_crossover")
        assert isinstance(result.backtest_id, str)
        assert len(result.backtest_id) == 64
        assert result.strategy_type == "sma_crossover"
        assert result.contract.freq == "1d"
        assert result.universe == ["ASSET"]
        assert "total_return_pct" in result.metrics
        assert "portfolio" in result.metrics
        assert len(result.equity_curve) + 1 == len(prices)

    def test_strategy_list_route_is_json_serializable(self) -> None:
        contract = HistoricalDataContract(freq="1d", start="2024-01-01", end="2024-12-31")
        prices = _demo_prices(120)
        result = run_historical_backtest(contract=contract, prices=prices, strategy_type="sma_crossover")
        payload = {
            "backtest_id": result.backtest_id,
            "strategy_type": result.strategy_type,
            "parameters": result.params,
            "contract": result.contract.__dict__,
            "universe": result.universe,
            "metrics": result.metrics,
            "equity_curve": result.equity_curve,
            "trade_log": result.trade_log,
            "positions": result.positions,
            "cost_summary": result.cost_summary,
        }
        assert json.dumps(payload)

    def test_breakout_strategy_runs(self) -> None:
        contract = HistoricalDataContract(freq="1d", start="2024-01-01", end="2024-12-31")
        prices = _demo_prices(120)
        result = run_historical_backtest(contract=contract, prices=prices, strategy_type="breakout")
        assert result.strategy_type == "breakout"
        assert "total_return_pct" in result.metrics

    def test_rsi_reversion_strategy_runs(self) -> None:
        contract = HistoricalDataContract(freq="1d", start="2024-01-01", end="2024-12-31")
        prices = _demo_prices(120)
        result = run_historical_backtest(contract=contract, prices=prices, strategy_type="rsi_reversion")
        assert result.strategy_type == "rsi_reversion"
        assert "total_return_pct" in result.metrics

    def test_rejects_insufficient_prices(self) -> None:
        contract = HistoricalDataContract(freq="1d", start="2024-01-01", end="2024-12-31")
        with pytest.raises(ValueError, match="20 price points"):
            run_historical_backtest(contract=contract, prices=[100.0] * 10, strategy_type="sma_crossover")

    def test_rejects_empty_prices(self) -> None:
        contract = HistoricalDataContract(freq="1d", start="2024-01-01", end="2024-12-31")
        with pytest.raises(ValueError, match="20 price points"):
            run_historical_backtest(contract=contract, prices=[], strategy_type="sma_crossover")

    def test_deterministic_contract_hash(self) -> None:
        contract = HistoricalDataContract(freq="1d", start="2024-01-01", end="2024-12-31", assets=["X", "Y"])
        prices = _demo_prices(120)
        first = run_historical_backtest(contract=contract, prices=prices, strategy_type="sma_crossover")
        second = run_historical_backtest(contract=contract, prices=prices, strategy_type="sma_crossover")
        assert first.backtest_id == second.backtest_id

    def test_report_schema_serializes(self) -> None:
        contract = HistoricalDataContract(freq="1d", start="2024-01-01", end="2024-12-31")
        prices = _demo_prices(120)
        result = run_historical_backtest(contract=contract, prices=prices, strategy_type="sma_crossover")
        schema = backtest_report_to_dict(result)
        assert set(schema.keys()) == {
            "backtest_id",
            "strategy_type",
            "parameters",
            "contract",
            "universe",
            "metrics",
            "equity_curve",
            "trade_log",
            "positions",
            "cost_summary",
        }
        assert json.dumps(schema)


class TestMultiAssetAccount:
    def test_multi_asset_universe_assignment(self) -> None:
        contract = HistoricalDataContract(
            freq="1d", start="2024-01-01", end="2024-12-31", assets=["A", "B", "C"]
        )
        prices = _demo_prices(120)
        result = run_historical_backtest(contract=contract, prices=prices, strategy_type="sma_crossover")
        assert result.universe == ["A", "B", "C"]
        assert result.metrics["portfolio"]["asset_count"] == 3

    def test_portfolio_summary_fields(self) -> None:
        contract = HistoricalDataContract(freq="1d", start="2024-01-01", end="2024-12-31")
        prices = _demo_prices(120)
        result = run_historical_backtest(contract=contract, prices=prices, strategy_type="sma_crossover")
        portfolio = result.metrics["portfolio"]
        assert portfolio["initial_capital"] == 1_000_000.0
        assert "final_portfolio_value" in portfolio
        assert "gross_exposure" in portfolio
        assert "net_exposure" in portfolio

    def test_equity_curve_length_consistent_with_metrics(self) -> None:
        contract = HistoricalDataContract(freq="1d", start="2024-01-01", end="2024-12-31")
        prices = _demo_prices(150)
        result = run_historical_backtest(contract=contract, prices=prices, strategy_type="sma_crossover")
        assert len(result.equity_curve) + 1 == len(prices)

    def test_multi_asset_fallback_assets(self) -> None:
        contract = HistoricalDataContract(freq="1d", start="2024-01-01", end="2024-12-31")
        prices = [100.0 + i * 0.1 for i in range(80)]
        result = run_historical_backtest(
            contract=contract,
            prices=prices,
            strategy_type="sma_crossover",
            universe=["A", "B"],
        )
        assert result.universe == ["A", "B"]
        assert result.metrics["portfolio"]["asset_count"] == 2


class TestExpandedMetrics:
    def test_expanded_metrics_keys(self) -> None:
        prices = _demo_prices(180)
        positions = [1.0 if i > 90 else 0.0 for i in range(len(prices))]
        result = HistoricalMetricsEngine.compute(
            [], prices=prices, positions=positions, benchmark_prices=_demo_prices(180), trial_count=25
        )
        for key in {
            "calmar",
            "calmar_warning",
            "var_95",
            "cvar_95",
            "var_99",
            "cvar_99",
            "var_cvar_warning",
            "turnover",
            "hit_rate",
            "hit_rate_warning",
            "profit_factor",
            "profit_factor_warning",
            "concentration",
            "concentration_warning",
            "gross_exposure",
            "gross_exposure_warning",
            "net_exposure",
            "net_exposure_warning",
            "bootstrap_sharpe_ci",
            "bootstrap_cagr_ci",
            "bootstrap_warning",
            "psr",
            "psr_threshold",
            "psr_warning",
            "benchmark_cagr",
            "benchmark_sharpe",
            "benchmark_warning",
            "information_ratio",
            "information_ratio_warning",
        }:
            assert key in result

    def test_no_nan_inf_propagates(self) -> None:
        prices = _demo_prices(120)
        positions = [1.0 if i > 60 else 0.0 for i in range(len(prices))]
        result = HistoricalMetricsEngine.compute(
            [], prices=prices, positions=positions, benchmark_prices=prices
        )
        for value in result.values():
            if isinstance(value, float):
                assert math.isfinite(value)

    def test_bootstrap_ci_keys_and_warning(self) -> None:
        prices = _demo_prices(180)
        positions = [1.0 if i > 90 else 0.0 for i in range(len(prices))]
        result = HistoricalMetricsEngine.compute([], prices=prices, positions=positions, bootstrap_seed=1)
        assert "bootstrap_warning" in result
        assert result["bootstrap_sharpe_ci"] is not None
        assert result["bootstrap_cagr_ci"] is not None

    def test_psr_without_trials(self) -> None:
        prices = _demo_prices(180)
        positions = [1.0 if i > 90 else 0.0 for i in range(len(prices))]
        result = HistoricalMetricsEngine.compute([], prices=prices, positions=positions)
        assert "psr_threshold" in result
        assert "psr_warning" in result

    def test_psr_bonferroni_threshold(self) -> None:
        prices = _demo_prices(120)
        positions = [1.0 if i > 60 else 0.0 for i in range(len(prices))]
        with patch.object(HistoricalMetricsEngine, "_norm_ppf", return_value=1.234) as mocked_norm_ppf:
            result = HistoricalMetricsEngine.compute([], prices=prices, positions=positions, trial_count=30)
        mocked_norm_ppf.assert_called_once()
        expected_call = (1.0 - 0.05 / 30,)
        actual_call = mocked_norm_ppf.call_args.args
        assert actual_call == expected_call
        assert "Bonferroni" in (result.get("psr_warning") or "")


class TestNextOpenExecution:
    def test_target_position_sets_executed_to_target(self) -> None:
        contract = HistoricalDataContract(freq="1d", start="2024-01-01", end="2024-12-31")
        prices = [100.0 + i for i in range(120)]
        result = run_historical_backtest(contract=contract, prices=prices, strategy_type="sma_crossover")
        for pos in result.positions:
            assert abs(pos["raw_target"] - pos["next_open_executed"]) < 1e-9

    def test_trade_log_records_transitions(self) -> None:
        contract = HistoricalDataContract(freq="1d", start="2024-01-01", end="2024-12-31")
        prices = _demo_prices(120)
        result = run_historical_backtest(contract=contract, prices=prices, strategy_type="sma_crossover")
        for trade in result.trade_log:
            assert "asset" in trade
            assert "raw_target" in trade
            assert "next_open_executed" in trade
            assert "commission_bps" in trade

    def test_cost_summary_keys_present(self) -> None:
        contract = HistoricalDataContract(freq="1d", start="2024-01-01", end="2024-12-31")
        prices = _demo_prices(120)
        result = run_historical_backtest(contract=contract, prices=prices, strategy_type="sma_crossover")
        assert "commission" in result.cost_summary
        assert "slippage" in result.cost_summary
        assert "total" in result.cost_summary


class TestTransactionCosts:
    def test_explicit_cost_model_changes_metrics(self) -> None:
        contract = HistoricalDataContract(freq="1d", start="2024-01-01", end="2024-12-31")
        prices = np.linspace(100, 120, 120).tolist()
        baseline = run_historical_backtest(contract=contract, prices=prices, strategy_type="sma_crossover")
        costly = run_historical_backtest(
            contract=contract,
            prices=prices,
            strategy_type="sma_crossover",
            tcost_spec={"spread_bps": 10.0, "impact_beta": 0.5},
        )
        assert _expensive_metric(costly) <= _expensive_metric(baseline)


class TestBacktestEndpointPresent:
    def test_backtest_router_exposes_routes(self) -> None:
        from app.strategy_lab.api.backtests import router as backtests_router

        paths = sorted(route.path for route in backtests_router.routes if hasattr(route, "path"))
        assert "/backtests" in paths
