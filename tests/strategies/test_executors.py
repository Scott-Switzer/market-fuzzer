"""Executor reference + metamorphic + e2e tests (reset brief item 28)."""

from __future__ import annotations

import numpy as np
import pytest

import app.strategies.executors  # noqa: F401  registers executors
from app.compiler import apply_resolutions, compile_thesis
from app.domain.strategy_spec import (
    MomentumSignal,
    PortfolioConstruction,
    RealizedVolatilitySignal,
    RiskConstraints,
    StrategySpec,
    StrategyType,
    Weighting,
)
from app.domain.strategy_version import DraftStrategy
from app.strategies.pipeline import run_strategy
from app.strategies.registry import default_registry
from app.strategies.templates import long_only_momentum, sma_crossover, static_6040
from tests.strategies.conftest import gbm_panel, make_panel

SUP = default_registry.supported_types()


# --- static allocation ------------------------------------------------------
def test_static_6040_targets_are_60_40(two_asset_panel):
    spec = static_6040("VOO", "BND")
    res = run_strategy(spec, two_asset_panel)
    # executed weights on any post-warmup decision-carry row are ~0.6/0.4
    row = res.executed_weights[100]
    assert abs(row[0] - 0.6) < 1e-9 and abs(row[1] - 0.4) < 1e-9


def test_static_6040_vs_7030_differ_everywhere(two_asset_panel):
    s60 = static_6040("VOO", "BND")
    s70 = StrategySpec(
        name="70/30",
        original_thesis="allocate 70 percent VOO 30 percent BND monthly",
        strategy_type=StrategyType.STATIC_ALLOCATION,
        universe=["VOO", "BND"],
        portfolio_construction=PortfolioConstruction(
            weighting=Weighting.FIXED, target_weights={"VOO": "0.7", "BND": "0.3"}
        ),
    )
    assert s60.compute_hash() != s70.compute_hash()
    r60 = run_strategy(s60, two_asset_panel)
    r70 = run_strategy(s70, two_asset_panel)
    assert not np.allclose(r60.executed_weights[100], r70.executed_weights[100])
    assert r60.metrics["final_equity"] != r70.metrics["final_equity"]


def test_static_allocation_does_not_run_momentum(two_asset_panel):
    # 60/40 must never produce long/short momentum trades
    res = run_strategy(static_6040("VOO", "BND"), two_asset_panel)
    assert not (res.executed_weights < -1e-9).any()  # no shorts ever


# --- sma crossover ----------------------------------------------------------
def test_sma_crossover_long_or_cash():
    # rising then falling series so we exercise both states
    up = np.linspace(100, 200, 120)
    down = np.linspace(200, 120, 120)
    close = np.concatenate([up, down]).reshape(-1, 1)
    panel = make_panel(["QQQ"], close)
    spec = sma_crossover("QQQ", 20, 50)
    res = run_strategy(spec, panel)
    ew = res.executed_weights[:, 0]
    assert set(np.unique(np.round(ew[ew != 0], 6))).issubset({1.0})  # only long or flat
    assert (ew == 0).any() and (ew == 1.0).any()  # both states occur


def test_sma_crossover_does_not_rank_universe():
    # single asset only; a stock universe would fail validation
    close = np.linspace(100, 200, 200).reshape(-1, 1)
    panel = make_panel(["QQQ"], close)
    res = run_strategy(sma_crossover("QQQ", 10, 30), panel)
    assert res.executed_weights.shape[1] == 1


# --- long only ranking ------------------------------------------------------
def test_long_only_ranking_never_shorts():
    assets = [f"S{i}" for i in range(8)]
    panel = gbm_panel(assets, seed=3)
    spec = long_only_momentum(assets, 3)
    res = run_strategy(spec, panel)
    assert not (res.executed_weights < -1e-9).any()


# --- cross sectional --------------------------------------------------------
def _ls_spec(assets):
    return StrategySpec(
        name="ls",
        original_thesis="long top short bottom by momentum monthly composite low vol",
        strategy_type=StrategyType.CROSS_SECTIONAL_FACTOR,
        universe=assets,
        signal_definitions=[MomentumSignal(id="m"), RealizedVolatilitySignal(id="v")],
        signal_combination={"momentum": "0.75", "low_volatility": "0.25"},
        portfolio_construction=PortfolioConstruction(
            long_short=True, long_quantile="0.2", short_quantile="0.2"
        ),
        risk_constraints=RiskConstraints(
            gross_exposure_limit="1.0", net_exposure_target="0.0", max_position_weight="0.5"
        ),
    )


def test_cross_sectional_creates_both_sides():
    assets = [f"S{i}" for i in range(10)]
    panel = gbm_panel(assets, seed=5)
    res = run_strategy(_ls_spec(assets), panel)
    ew = res.executed_weights
    assert (ew > 1e-9).any() and (ew < -1e-9).any()


def test_cross_sectional_long_short_disjoint():
    assets = [f"S{i}" for i in range(10)]
    panel = gbm_panel(assets, seed=5)
    res = run_strategy(_ls_spec(assets), panel)
    tw = res.target_weights
    for t in range(tw.shape[0]):
        longs = tw[t] > 1e-9
        shorts = tw[t] < -1e-9
        assert not (longs & shorts).any()


# --- tactical ---------------------------------------------------------------
def test_tactical_uses_fallback_when_nothing_passes():
    # all risky assets in a downtrend, fallback flat -> fallback should be held
    T = 400
    down = np.linspace(200, 80, T)
    risky = np.column_stack([down, down * 1.01, down * 0.99])
    bil = np.full(T, 100.0)  # flat cash-like
    close = np.column_stack([risky, bil.reshape(-1, 1)])
    assets = ["SPY", "EFA", "EEM", "BIL"]
    panel = make_panel(assets, close, benchmark_close=close[:, 0].copy())
    spec = StrategySpec(
        name="taa",
        original_thesis="top asset ETFs by 12m momentum above 200d else BIL monthly",
        strategy_type=StrategyType.TACTICAL_ALLOCATION,
        universe=assets,
        lookback_windows={"return": 252, "trend": 200},
        ranking_or_threshold_rules={"top_k": 2, "fallback": "BIL"},
    )
    res = run_strategy(spec, panel)
    bil_idx = assets.index("BIL")
    # after warmup, fallback should be held on decision-carry rows
    assert (res.executed_weights[300:, bil_idx] > 0.9).any()


# --- metamorphic ------------------------------------------------------------
def test_higher_costs_cannot_improve_equity(two_asset_panel):
    from app.strategies.accounting import CostModel, run_accounting
    from app.strategies.executors._base import context_from_panel

    spec = static_6040("VOO", "BND")
    ctx = context_from_panel(two_asset_panel)
    plan = default_registry.get(spec.strategy_type).build_targets(spec, ctx)
    common = dict(
        plan=plan,
        open_=two_asset_panel.open,
        close=two_asset_panel.close,
        dates=[d.isoformat() for d in two_asset_panel.dates],
        assets=list(two_asset_panel.assets),
    )
    low = run_accounting(
        **common,
        cost_model=CostModel(commission_bps=0, spread_bps=0, slippage_bps=0, borrow_bps=0, locate_bps=0),
    )
    high = run_accounting(
        **common,
        cost_model=CostModel(
            commission_bps=50, spread_bps=20, slippage_bps=20, borrow_bps=100, locate_bps=50
        ),
    )
    assert high.equity_curve[-1] <= low.equity_curve[-1] + 1e-6


def test_changing_windows_changes_hash():
    a = sma_crossover("SPY", 20, 50)
    b = sma_crossover("SPY", 10, 40)
    assert a.compute_hash() != b.compute_hash()


def test_permuting_columns_preserves_ticker_ranks():
    assets = [f"S{i}" for i in range(6)]
    panel = gbm_panel(assets, seed=11)
    spec = long_only_momentum(assets, 2)
    res1 = run_strategy(spec, panel)
    # permute columns
    perm = [3, 0, 5, 1, 4, 2]
    passets = [assets[i] for i in perm]
    pclose = panel.close[:, perm]
    ppanel = make_panel(passets, pclose, open_=panel.open[:, perm], benchmark_close=panel.benchmark_close)
    spec2 = long_only_momentum(passets, 2)
    res2 = run_strategy(spec2, ppanel)

    # the SAME tickers should be selected regardless of column order
    def held(res):
        held_sets = []
        for t in range(res.target_weights.shape[0]):
            names = {res.assets[i] for i in range(len(res.assets)) if res.target_weights[t, i] > 1e-9}
            if names:
                held_sets.append(frozenset(names))
        return held_sets

    assert held(res1) == held(res2)


def test_same_inputs_deterministic(two_asset_panel):
    spec = static_6040("VOO", "BND")
    r1 = run_strategy(spec, two_asset_panel)
    r2 = run_strategy(spec, two_asset_panel)
    assert r1.backtest_id == r2.backtest_id
    assert np.array_equal(r1.equity_curve, r2.equity_curve)


# --- e2e --------------------------------------------------------------------
def test_e2e_compile_resolve_approve_run_hash_stable():
    thesis = "Buy the top 3 stocks by 12-1 momentum each month."
    comp = compile_thesis(thesis)
    comp = apply_resolutions(comp, {"universe": [f"S{i}" for i in range(8)]})
    draft = DraftStrategy(spec=comp.strategy_spec_draft)
    approved = draft.approve(approved_by="scott", supported_types=SUP)
    reconstructed = approved.to_spec()
    # hash stable across compile -> approve -> reconstruct
    assert comp.strategy_spec_draft.compute_hash() == approved.canonical_hash
    assert reconstructed.compute_hash() == approved.canonical_hash
    # run it
    panel = gbm_panel([f"S{i}" for i in range(8)], seed=2)
    res = run_strategy(reconstructed, panel, expected_hash=approved.canonical_hash)
    assert res.strategy_hash == approved.canonical_hash


def test_e2e_hash_drift_blocks_execution():
    panel = gbm_panel(["A", "B", "C", "D", "E"], seed=1)
    spec = long_only_momentum(["A", "B", "C", "D", "E"], 2)
    with pytest.raises(ValueError, match="drift"):
        run_strategy(spec, panel, expected_hash="0" * 64)


def test_unsupported_type_blocks_execution():
    panel = gbm_panel(["A", "B", "C"], seed=1)
    spec = StrategySpec(
        name="x",
        original_thesis="something unsupported entirely here",
        strategy_type=StrategyType.UNSUPPORTED,
        universe=["A", "B", "C"],
    )
    from app.strategies.errors import UnknownStrategyType

    with pytest.raises((ValueError, UnknownStrategyType)):
        run_strategy(spec, panel)
