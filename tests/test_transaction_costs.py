import math

import numpy as np
import pytest

from app.break_test.costs import TransactionCostModel
from app.break_test.metrics import backtest_metrics, compute_equity_curve


def _prices(n: int = 120) -> np.ndarray:
    rng = np.random.default_rng(0)
    return np.asarray((100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))), dtype=float)


class TestTransactionCostModel:
    def test_short_inventory_borrow_fee_applied(self) -> None:
        prices = _prices()
        positions = np.where(np.arange(len(prices)) % 40 == 0, -1.0, 0.0)
        flat_model = TransactionCostModel(spread_bps=2.0, borrow_fee_bps=0.0, impact_beta=0.0, default_adv=100_000.0)
        borrow_model = TransactionCostModel(spread_bps=2.0, borrow_fee_bps=10.0, impact_beta=0.0, default_adv=100_000.0)
        flat_curve = compute_equity_curve(prices, positions, tcost_model=flat_model, default_adv=100_000.0)
        borrow_curve = compute_equity_curve(prices, positions, tcost_model=borrow_model, default_adv=100_000.0)
        assert borrow_curve[-1] < flat_curve[-1] - 1e-9

    def test_sell_signal_with_borrow_fee(self) -> None:
        prices = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
        positions = np.array([0.0, 0.0, -1.0, -1.0, 1.0, 1.0])
        tcost = TransactionCostModel(spread_bps=2.0, borrow_fee_bps=12.0, impact_beta=0.0, default_adv=100_000.0)
        curve = compute_equity_curve(prices, positions, tcost_model=tcost, default_adv=100_000.0)
        no_borrow = TransactionCostModel(spread_bps=2.0, borrow_fee_bps=0.0, impact_beta=0.0, default_adv=100_000.0)
        baseline = compute_equity_curve(prices, positions, tcost_model=no_borrow, default_adv=100_000.0)
        assert curve[-1] < baseline[-1] - 1e-9

    def test_high_participation_nonlinear_impact(self) -> None:
        prices = np.linspace(100, 120, 500)
        large_trades = np.where(np.arange(500) % 40 == 0, 1.0, 0.0)
        small_trades = np.where(np.arange(500) % 40 == 0, 0.1, 0.0)

        tcost = TransactionCostModel(spread_bps=2.0, borrow_fee_bps=0.0, impact_beta=0.5, impact_mode="sqrt", default_adv=10_000.0)
        large_cost = tcost.costs_for_signals(prices.tolist(), large_trades.tolist(), default_adv=10_000.0)
        small_cost = tcost.costs_for_signals(prices.tolist(), small_trades.tolist(), default_adv=10_000.0)

        trade_mask = np.abs(np.diff(large_trades)) > 0
        large_at_trades = np.abs(large_cost[trade_mask])
        small_at_trades = np.abs(small_cost[trade_mask])
        assert float(np.mean(large_at_trades)) > float(np.mean(small_at_trades)) * 2.0

        tcost_linear = TransactionCostModel(spread_bps=2.0, borrow_fee_bps=0.0, impact_beta=0.5, impact_mode="linear", default_adv=10_000.0)
        linear_large = tcost_linear.costs_for_signals(prices.tolist(), large_trades.tolist(), default_adv=10_000.0)
        linear_at_trades = np.abs(linear_large[trade_mask])
        assert float(np.mean(large_at_trades)) > 1e-9
        assert float(np.mean(linear_at_trades)) > 1e-9
        assert abs(float(np.mean(large_at_trades)) - float(np.mean(linear_at_trades))) > 1e-9

    def test_backward_compatible_when_disabled(self) -> None:
        prices = _prices()
        positions = np.ones_like(prices)
        legacy = backtest_metrics(prices, positions, fee_bps=2.0)
        curve_legacy = compute_equity_curve(prices, positions, fee_bps=2.0)
        flat_model = TransactionCostModel(spread_bps=4.0, borrow_fee_bps=0.0, impact_beta=0.0, default_adv=100_000.0)
        explicit = backtest_metrics(prices, positions, tcost_model=flat_model, default_adv=100_000.0)
        curve_explicit = compute_equity_curve(prices, positions, tcost_model=flat_model, default_adv=100_000.0)
        assert math.isclose(legacy["total_return_pct"], explicit["total_return_pct"], rel_tol=1e-6)
        assert math.isclose(curve_legacy[-1], curve_explicit[-1], rel_tol=1e-6)
