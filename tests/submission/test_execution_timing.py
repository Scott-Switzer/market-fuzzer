"""Test-execution-timing (spec 3.1).

The engine must NOT trade at open t using information from close t.
Features + target weights are computed after close t; fills occur at
open t+1 (or t+1+delay). Mutating close[t] must not change the
fill at t; the first possible changed fill is at t+1 (or later).
"""

from __future__ import annotations

import numpy as np

from app.strategy_lab.submission.engine import run_portfolio_backtest
from app.strategy_lab.submission.fixture import build_fixture_panel
from app.strategy_lab.submission.strategy import CrossSectionalSpec


def _spec():
    return CrossSectionalSpec(
        universe=["SYN_A", "SYN_B", "SYN_C", "SYN_D", "SYN_E", "SYN_F", "SPY"],
        benchmark="SPY",
        momentum_lookback=120,
        momentum_short=21,
        volatility_window=30,
        long_quantile=0.5,
        short_quantile=0.5,
        gross_exposure=0.5,
        net_exposure=0.0,
        max_position_weight=0.10,
    )


def _run(panel):
    return run_portfolio_backtest(panel=panel, spec=_spec(), strategy_hash="h")


def test_fill_at_t_unchanged_by_close_t_mutation():
    """Mutate close at row t -> fills at row t must be identical."""
    base = build_fixture_panel()
    res_base = _run(base)
    # pick a mid-row where both base and mutated have valid (possibly zero) fills
    t = 200
    mut = build_fixture_panel()
    c = mut.close.copy()
    c[t] = c[t] * 1.5  # change close at t (affects feature/signal for t, not fill at t)
    from app.strategy_lab.submission.panels import MarketDataPanel

    mut_panel = MarketDataPanel(
        dates=mut.dates,
        assets=mut.assets,
        open=mut.open,
        high=mut.high,
        low=mut.low,
        close=c,
        volume=mut.volume,
        benchmark_close=mut.benchmark_close,
        metadata=mut.metadata,
        provenance=mut.provenance,
    )
    res_mut = _run(mut_panel)
    # fill at t uses signal from t-1 (and earlier), so close[t] cannot change it
    assert np.allclose(res_base.shares[t], res_mut.shares[t]), (
        f"fill at t={t} changed by mutating close[t] -> lookahead leak"
    )


def test_first_changed_fill_is_t_plus_1_or_later():
    base = build_fixture_panel()
    res_base = _run(base)
    t = 200
    mut = build_fixture_panel()
    c = mut.close.copy()
    c[t] = c[t] * 1.5
    from app.strategy_lab.submission.panels import MarketDataPanel

    mut_panel = MarketDataPanel(
        dates=mut.dates,
        assets=mut.assets,
        open=mut.open,
        high=mut.high,
        low=mut.low,
        close=c,
        volume=mut.volume,
        benchmark_close=mut.benchmark_close,
        metadata=mut.metadata,
        provenance=mut.provenance,
    )
    res_mut = _run(mut_panel)
    # The signal produced at t feeds the fill at t+1 (or later). So the first
    # row where fills may differ is t+1. Confirm exactly that differs (not t).
    same_at_t = np.allclose(res_base.shares[t], res_mut.shares[t])
    differ_at_tp1 = not np.allclose(res_base.shares[t + 1], res_mut.shares[t + 1])
    assert same_at_t
    assert differ_at_tp1, "expected first diff at t+1 (signal from t fills t+1)"


def test_first_row_has_no_position_no_prior_signal():
    base = build_fixture_panel()
    res = _run(base)
    # row 0 has no prior signal -> no position
    assert np.all(res.shares[0] == 0)


def test_delay_shifts_fills_by_n_days():
    base = build_fixture_panel()
    spec_delay = _spec()
    spec_delay = spec_delay.__class__(**{**spec_delay.__dict__, "execution_delay_days": 3})
    res = _run(base)
    res_delay = run_portfolio_backtest(panel=base, spec=spec_delay, strategy_hash="h")
    # delayed fills must not equal the non-delayed fills at the same row
    assert not np.allclose(res.shares[205], res_delay.shares[205]), "execution delay must shift fills"
