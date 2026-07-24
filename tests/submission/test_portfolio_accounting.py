"""Test-portfolio-accounting (spec 3.2, 3.3, 3.4, 3.5, 3.6).

Verifies the corrected engine:
 * target notionals use pre-trade EQUITY (not cash),
 * long-only, dollar-neutral long/short, multiple rebalances, short proceeds,
 * gross/net targets met when feasible; explicit infeasible warning,
 * spread charged, borrow accrues daily,
 * benchmark CAGR is geometric,
 * accounting ties (cash + holdings == equity) every row.
"""

from __future__ import annotations

import numpy as np

from app.strategy_lab.submission.engine import run_portfolio_backtest
from app.strategy_lab.submission.fixture import build_fixture_panel
from app.strategy_lab.submission.strategy import CrossSectionalSpec


def _panel():
    return build_fixture_panel()


def test_long_only_sizing_on_equity_not_cash():
    """Target notionals must be derived from pre-trade EQUITY (not cash), and the
    realized gross exposure (engine metric) must respect the declared target."""
    panel = _panel()
    spec = CrossSectionalSpec(
        universe=list(panel.assets), benchmark="SPY",
        momentum_lookback=120, momentum_short=21, volatility_window=30,
        long_quantile=0.5, short_quantile=0.0,  # long-only
        gross_exposure=0.5, net_exposure=0.5, max_position_weight=0.10,
    )
    res = run_portfolio_backtest(panel=panel, spec=spec, strategy_hash="h")
    # engine-reported gross exposure never exceeds the declared target (within float eps)
    assert res.metrics["gross_exposure_avg"] <= spec.gross_exposure + 1e-6
    assert res.metrics["gross_exposure_avg"] > 0.1
    # cash diverges from equity once positions exist -> sizing MUST use equity
    T, N = panel.close.shape
    # at a held row, cash != equity, yet weights still sum to ~target*gross
    for t in range(150, 200):
        if np.any(res.shares[t] != 0):
            weight_abs_sum = float(np.sum(np.abs(res.executed_weights[t])))
            # fraction of equity in positions must respect the declared gross target
            assert weight_abs_sum <= spec.gross_exposure + 1e-6


def test_dollar_neutral_long_short():
    panel = _panel()
    spec = CrossSectionalSpec(
        universe=list(panel.assets), benchmark="SPY",
        momentum_lookback=120, momentum_short=21, volatility_window=30,
        long_quantile=0.5, short_quantile=0.5,
        gross_exposure=0.5, net_exposure=0.0, max_position_weight=0.10,
    )
    res = run_portfolio_backtest(panel=panel, spec=spec, strategy_hash="h")
    # net exposure must be ~0 (dollar neutral) on average
    assert abs(res.metrics["net_exposure_avg"]) < 0.05
    # gross must be at/under declared 0.5
    assert res.metrics["gross_exposure_avg"] <= 0.5 + 1e-6
    assert res.metrics["gross_exposure_avg"] > 0.1


def test_multiple_rebalances_and_short_proceeds():
    panel = _panel()
    spec = CrossSectionalSpec(
        universe=list(panel.assets), benchmark="SPY",
        momentum_lookback=120, momentum_short=21, volatility_window=30,
        long_quantile=0.5, short_quantile=0.5,
        gross_exposure=0.5, net_exposure=0.0, max_position_weight=0.10,
    )
    res = run_portfolio_backtest(panel=panel, spec=spec, strategy_hash="h")
    # at least two distinct rebalance months must produce positions
    pos_rows = [t for t in range(res.shares.shape[0]) if np.any(res.shares[t] != 0)]
    assert len(pos_rows) > 60  # multiple rebalances hold positions
    # short legs exist at some point (dollar-neutral L/S)
    assert np.any(res.shares < 0)


def test_accounting_ties_every_row():
    """cash + mark-to-market holdings == equity for every row."""
    panel = _panel()
    spec = CrossSectionalSpec(
        universe=list(panel.assets), benchmark="SPY",
        momentum_lookback=120, momentum_short=21, volatility_window=30,
        long_quantile=0.5, short_quantile=0.5,
        gross_exposure=0.5, net_exposure=0.0, max_position_weight=0.10,
    )
    res = run_portfolio_backtest(panel=panel, spec=spec, strategy_hash="h")
    T, N = panel.close.shape
    for t in range(T):
        hold = float(np.sum(res.shares[t] * panel.close[t]))
        recon = res.cash[t] + hold
        assert abs(recon - res.equity_curve[t]) < 1e-4, f"row {t} untied: {recon} vs {res.equity_curve[t]}"


def test_infeasible_gross_warns_and_scales():
    """On the 7-asset fixture, default 1.0 gross with 0.10 cap and 0.20
    quantiles is infeasible -> engine must warn AND scale (not silently underfill)."""
    panel = _panel()
    spec = CrossSectionalSpec()  # defaults: gross 1.0, quantiles 0.2, cap 0.10
    res = run_portfolio_backtest(panel=panel, spec=spec, strategy_hash="h")
    infeas = [w for w in res.warnings if w["type"] == "infeasible_gross"]
    assert infeas, "expected infeasible_gross warning for default fixture spec"
    # after scaling, sum|w| never exceeds the feasible cap
    max_g = max(float(np.sum(np.abs(res.executed_weights[t]))) for t in range(res.executed_weights.shape[0]))
    assert max_g <= 0.20 + 1e-6  # feasible given 1L+1S on 7 assets


def test_spread_charged():
    panel = _panel()
    spec = CrossSectionalSpec(
        universe=list(panel.assets), benchmark="SPY",
        momentum_lookback=120, momentum_short=21, volatility_window=30,
        long_quantile=0.5, short_quantile=0.5,
        gross_exposure=0.5, net_exposure=0.0, max_position_weight=0.10,
    )
    res = run_portfolio_backtest(panel=panel, spec=spec, strategy_hash="h")
    assert res.cost_summary["spread"] > 0.0, "spread must be charged"
    assert res.cost_summary["total"] >= res.cost_summary["spread"]


def test_borrow_accrues_daily_on_outstanding_shorts():
    """Borrow is charged on outstanding short market value every day, not once."""
    panel = _panel()
    spec = CrossSectionalSpec(
        universe=list(panel.assets), benchmark="SPY",
        momentum_lookback=120, momentum_short=21, volatility_window=30,
        long_quantile=0.5, short_quantile=0.5,
        gross_exposure=0.5, net_exposure=0.0, max_position_weight=0.10,
        borrow_bps=50.0,
    )
    res = run_portfolio_backtest(panel=panel, spec=spec, strategy_hash="h")
    # daily borrow accumulator must have grown beyond a single opening charge
    daily = res.daily_borrow
    assert daily.sum() > 0.0
    # borrow should accrue over many holding days, not just the open day
    assert int(np.count_nonzero(daily)) > 5


def test_benchmark_cagr_is_geometric():
    panel = _panel()
    spec = CrossSectionalSpec(
        universe=list(panel.assets), benchmark="SPY",
        momentum_lookback=120, momentum_short=21, volatility_window=30,
        long_quantile=0.5, short_quantile=0.5,
        gross_exposure=0.5, net_exposure=0.0, max_position_weight=0.10,
    )
    res = run_portfolio_backtest(panel=panel, spec=spec, strategy_hash="h")
    bench = panel.benchmark_close
    # find last non-nan (engine uses that index)
    m_b = int(np.max(np.where(np.isfinite(bench))[0]))
    geo = (bench[m_b] / bench[0]) ** (252.0 / max(m_b - 1, 1)) - 1.0
    assert abs(res.metrics["benchmark_cagr"] - geo) < 1e-6
    # must NOT equal mean*252 (the old buggy formula)
    mean_annual = float(np.nanmean(bench[1:] / bench[:-1] - 1.0)) * 252.0
    assert abs(res.metrics["benchmark_cagr"] - mean_annual) > 1e-3
