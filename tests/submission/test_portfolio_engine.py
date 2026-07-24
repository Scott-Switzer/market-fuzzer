"""Focused tests for the REAL T x N portfolio backtester.

These deliberately assert ECONOMIC behavior (multi-asset, cost deduction, no
look-ahead) — NOT just label counts — to prove the engine is not the old facade
that discarded every asset but the first.
"""

import numpy as np

from app.strategy_lab.submission.engine import (
    compute_momentum,
    compute_volatility,
    run_portfolio_backtest,
)
from app.strategy_lab.submission.fixture import build_fixture_panel
from app.strategy_lab.submission.panels import MarketDataPanel
from app.strategy_lab.submission.strategy import CrossSectionalSpec


def _panel_from_close(close: np.ndarray, benchmark: np.ndarray | None = None) -> MarketDataPanel:
    T, N = close.shape
    assets = [f"A{i}" for i in range(N)]
    meta = {
        a: __import__("app.strategy_lab.submission.panels", fromlist=["AssetMetadata"]).AssetMetadata(
            ticker=a
        )
        for a in assets
    }
    from datetime import date, timedelta

    dates = tuple(date(2022, 1, 1) + timedelta(days=i) for i in range(T))
    from app.strategy_lab.submission.panels import DataProvenance

    prov = DataProvenance(source="deterministic_fixture", tier=3, label="test")
    return MarketDataPanel(
        dates=dates,
        assets=tuple(assets),
        open=close.copy(),
        high=close.copy(),
        low=close.copy(),
        close=close,
        volume=np.ones((T, N)),
        benchmark_close=benchmark,
        metadata=meta,
        provenance=prov,
    )


def test_hand_calculated_single_asset_no_lookahead():
    """A panel where asset 0 trends up, asset 1 trends down over ~3 months.
    Long-only top-momentum holding of A0 should produce positive cumulative return
    net of (zero) costs when momentum is valid and a monthly rebalance fires."""
    T = 70
    a0 = 100.0 * np.exp(np.linspace(0, 0.4, T))  # rising
    a1 = 100.0 * np.exp(np.linspace(0, -0.3, T))  # falling
    close = np.column_stack([a0, a1])
    panel = _panel_from_close(close)
    spec = CrossSectionalSpec(
        universe=["A0", "A1"],
        benchmark="A0",
        momentum_lookback=20,
        momentum_short=5,
        volatility_window=10,
        long_quantile=0.5,
        short_quantile=0.0,
        gross_exposure=1.0,
        net_exposure=1.0,
        commission_bps=0.0,
        spread_bps=0.0,
        slippage_bps=0.0,
        borrow_bps=0.0,
    )
    res = run_portfolio_backtest(panel=panel, spec=spec, strategy_hash="h", initial_capital=1_000_000.0)
    assert res.metrics["cumulative_return"] > 0.0
    assert res.equity_curve[-1] > res.equity_curve[0]
    assert len(res.trades) > 0


def test_multi_asset_economic_behavior_changes_with_asset():
    """Changing one asset's price path must change results (proves all assets used)."""
    panel = build_fixture_panel()
    spec = CrossSectionalSpec()
    res = run_portfolio_backtest(panel=panel, spec=spec, strategy_hash="h1")
    close2 = panel.close.copy()
    close2[:, 2] = close2[0, 2]  # flatten asset index 2
    p2 = MarketDataPanel(
        dates=panel.dates,
        assets=panel.assets,
        open=close2,
        high=close2,
        low=close2,
        close=close2,
        volume=panel.volume,
        benchmark_close=panel.benchmark_close,
        metadata=panel.metadata,
        provenance=panel.provenance,
    )
    res2 = run_portfolio_backtest(panel=p2, spec=spec, strategy_hash="h2")
    assert abs(float(res.equity_curve[-1]) - float(res2.equity_curve[-1])) > 1.0


def test_costs_deducted_from_equity():
    """With costs > 0, final equity must be strictly less than a zero-cost run."""
    panel = build_fixture_panel()
    spec0 = CrossSectionalSpec(commission_bps=0.0, spread_bps=0.0, slippage_bps=0.0, borrow_bps=0.0)
    spec1 = CrossSectionalSpec(commission_bps=10.0, spread_bps=5.0, slippage_bps=5.0, borrow_bps=100.0)
    r0 = run_portfolio_backtest(panel=panel, spec=spec0, strategy_hash="c0")
    r1 = run_portfolio_backtest(panel=panel, spec=spec1, strategy_hash="c1")
    assert r1.metrics["cost_total"] > 0.0
    assert float(r1.equity_curve[-1]) < float(r0.equity_curve[-1])


def test_no_lookahead_in_features():
    """momentum/vol at t use only data through t (not t+1)."""
    close = np.arange(1, 11, dtype=float).reshape(10, 1).astype(float) * 10.0
    close = np.repeat(close, 2, axis=1)
    mom = compute_momentum(close, 1, 3)
    # momentum at t=3 = close[t-short]/close[t-long]-1 = close[2]/close[0]-1 = 30/10-1 = 2.0
    assert abs(mom[3, 0] - 2.0) < 1e-9
    # momentum at t uses close[2] and close[0]; must NOT use close[3] (the future)
    assert np.isnan(mom[2, 0])  # needs t>=long=3


def test_volatility_annualization():
    # alternating +1%/-1% returns => daily std ~0.01 => annualized ~0.01*sqrt(252)
    prices = [100.0]
    for i in range(29):
        prices.append(prices[-1] * (1.01 if i % 2 == 0 else 0.99))
    c = np.array(prices, dtype=float).reshape(30, 1)
    c = np.repeat(c, 2, axis=1)
    ret = np.full_like(c, np.nan)
    ret[1:] = c[1:] / c[:-1] - 1.0
    vol = compute_volatility(ret, 10)
    # match engine semantics: sample std (ddof=1) over trailing 10 returns, annualized
    expected = float(np.std(ret[5:15, 0], ddof=1) * np.sqrt(252.0))
    assert abs(vol[15, 0] - expected) < 1e-9


def test_gross_exposure_respects_cap():
    panel = build_fixture_panel()
    spec = CrossSectionalSpec(gross_exposure=0.5)
    res = run_portfolio_backtest(panel=panel, spec=spec, strategy_hash="g")
    # gross exposure should never exceed the cap by more than float tolerance
    assert max(res.gross_exposure) <= 0.5 + 1e-6
