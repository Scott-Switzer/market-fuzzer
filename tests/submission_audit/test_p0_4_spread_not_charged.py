"""P0-4: spread_bps is accepted by the spec but NEVER charged.

_charge_costs deducts commission + slippage + borrow only. spec.spread_bps
(default 2.0) appears nowhere in the cost path, so every backtest understates
costs by half-spread * traded notional on every fill.

HAND CALC (spread the ONLY nonzero cost):
  spec: commission=slippage=borrow=0, spread_bps=10.
  First rebalance trades a [0.5, -0.5] book off ~$1,000,000 => traded notional
  ~= $1,000,000 (0.5M buy + 0.5M short).
  Expected spread cost >= 10/10_000 * 1_000_000 * 0.5 = $500 (conservative
  half-spread floor; even the loosest correct implementation charges > $0).
  ENGINE: cost_summary["total"] == 0.0 and equity is identical to a
  zero-cost run.

FIX SIGNATURE: _charge_costs must add
  `spread = spec.spread_bps / 10_000.0 * notional / 2.0`  (half-spread per side)
per trade, deduct it from cash, accumulate spread_total, and cost_summary must
gain a "spread" key included in "total".
"""

import numpy as np
import pytest
from _audit_helpers import make_panel, small_spec

from app.strategy_lab.submission.engine import run_portfolio_backtest


def _panel():
    T = 70
    a0 = 100.0 * np.exp(np.linspace(0.0, 0.4, T))
    a1 = 100.0 * np.exp(np.linspace(0.0, -0.3, T))
    return make_panel(np.column_stack([a0, a1]))


@pytest.mark.xfail(strict=False, reason="P0-4: spec.spread_bps never charged in _charge_costs")
def test_spread_is_charged():
    panel = _panel()
    spec = small_spec(spread_bps=10.0)  # all other costs zero
    res = run_portfolio_backtest(panel=panel, spec=spec, strategy_hash="p0-4", initial_capital=1_000_000.0)
    assert res.cost_summary["total"] > 0.0, (
        "SPREAD DEFECT: spread_bps=10 with ~$1M traded notional produced $0 of "
        "costs. _charge_costs never reads spec.spread_bps."
    )


@pytest.mark.xfail(strict=False, reason="P0-4: spread has no equity impact")
def test_spread_reduces_equity_vs_zero_spread():
    panel = _panel()
    r0 = run_portfolio_backtest(
        panel=panel,
        spec=small_spec(spread_bps=0.0),
        strategy_hash="p0-4a",
        initial_capital=1_000_000.0,
    )
    r1 = run_portfolio_backtest(
        panel=panel,
        spec=small_spec(spread_bps=10.0),
        strategy_hash="p0-4b",
        initial_capital=1_000_000.0,
    )
    assert float(r1.equity_curve[-1]) < float(r0.equity_curve[-1]), (
        "SPREAD DEFECT: final equity identical with and without a 10bp spread."
    )
