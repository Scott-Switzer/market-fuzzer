"""P0-3: Infeasible exposure config is silently underfilled.

With the DEFAULT spec (gross_exposure=1.0, long/short quantile=0.20,
max_position_weight=0.10) on a 7-asset universe:

HAND CALC:
  n_long  = max(1, round(0.20 * 7)) = max(1, 1) = 1
  n_short = max(1, round(0.20 * 7)) = 1
  long_each  = (1.0 / 2) / 1 = 0.50  -> clipped to max_position 0.10
  short_each = 0.50               -> clipped to -0.10
  realized gross = |0.10| + |-0.10| = 0.20  vs configured 1.00 (80% underfill)

The engine neither raises, rescales across more names, nor records a warning —
it silently runs a 0.2x book while reporting the strategy as gross=1.0. All
risk/return metrics are then computed on 5x less exposure than declared.

FIX SIGNATURE: cross_sectional_target_weights(...) must either
  (a) raise ValueError("infeasible: gross_exposure=G requires >= G/max_position
      positions per side; got n_long=..., n_short=...") when
      n_side * max_position < gross/2, or
  (b) widen selection so n_side >= ceil((gross/2)/max_position) and document it.
"""

import numpy as np
import pytest
from _audit_helpers import make_panel, small_spec

from app.strategy_lab.submission.engine import run_portfolio_backtest


@pytest.mark.xfail(strict=False, reason="P0-3: infeasible gross/quantile/cap config silently underfilled")
def test_default_exposure_config_feasible_or_rejected():
    T = 70
    N = 7
    rng = np.random.default_rng(7)
    drifts = np.linspace(-0.3, 0.4, N)
    close = np.exp(np.cumsum(rng.normal(0.0, 0.005, size=(T, N)) + drifts / T, axis=0)) * 100.0
    panel = make_panel(close)
    spec = small_spec(
        universe=[f"A{i}" for i in range(N)],
        long_quantile=0.20,
        short_quantile=0.20,
        gross_exposure=1.0,
        net_exposure=0.0,
        max_position_weight=0.10,
    )
    res = run_portfolio_backtest(panel=panel, spec=spec, strategy_hash="p0-3", initial_capital=1_000_000.0)
    tw = res.target_weights
    live = np.abs(tw).sum(axis=1) > 1e-12
    assert live.any(), "no live targets; panel misconfigured"
    realized_gross = float(np.abs(tw[live]).sum(axis=1).max())
    # Correct behavior: realized target gross must honor the configured gross
    # (within 10%) — or the engine should have raised on infeasibility.
    assert realized_gross >= 0.9 * spec.gross_exposure, (
        f"EXPOSURE DEFECT: configured gross={spec.gross_exposure}, max realized "
        f"target gross={realized_gross:.2f} (hand calc: 2 names x 0.10 cap = 0.20). "
        f"Engine silently runs a {realized_gross:.0%} book."
    )
