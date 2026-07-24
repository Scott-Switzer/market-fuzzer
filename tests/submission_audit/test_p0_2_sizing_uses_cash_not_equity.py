"""P0-2: Position sizing uses CASH instead of EQUITY (engine.py line ~350).

_weights_to_shares: `notional = weights * cash`. Target weights are fractions
of CAPITAL (equity), not of the cash balance. For any invested book,
cash != equity, so sizing is wrong; for a fully-invested long book it is
catastrophic: after the first fill cash ~ 0, so the NEXT rebalance targets
~zero shares and silently LIQUIDATES the entire portfolio.

HAND CALC (cap = 1,000,000, zero costs, weights [0.5, 0.5] net-long):
  - First rebalance t=31 (Feb 1): buy 0.5M of each asset. cash[31] ~ 0,
    equity[31] ~ 1,000,000 (all in stock).
  - Second rebalance t=59 (Mar 1): correct notional_n = 0.5 * equity[58]
    (~0.5M+PnL each). ENGINE: notional_n = 0.5 * cash[58] ~ 0.5 * $0 = $0
    -> target_shares ~ 0 -> the book is dumped and gross exposure collapses
    from ~1.0 to ~0.
  - Assert: a few days after the second rebalance the portfolio is still
    ~fully invested (market_value / equity >= 0.9).

FIX SIGNATURE: _weights_to_shares(weights, price, equity, mark) where
  equity = cash + sum(prev_shares * mark); i.e. call site passes
  `cash[t-1] + float(np.sum(prev_shares * close[t-1]))` instead of cash[t-1].
"""

import numpy as np
import pytest
from _audit_helpers import make_panel, small_spec

from app.strategy_lab.submission.engine import run_portfolio_backtest


@pytest.mark.xfail(strict=False, reason="P0-2: _weights_to_shares sizes off cash, not equity (engine.py ~350)")
def test_second_rebalance_sizes_off_equity_not_cash():
    T = 70
    a0 = 100.0 * np.exp(np.linspace(0.0, 0.2, T))
    a1 = 100.0 * np.exp(np.linspace(0.0, -0.1, T))
    panel = make_panel(np.column_stack([a0, a1]))
    # Fully-invested net-long book: weights become [0.5, 0.5].
    spec = small_spec(long_quantile=0.5, short_quantile=0.0, net_exposure=1.0)
    res = run_portfolio_backtest(
        panel=panel, spec=spec, strategy_hash="p0-2", initial_capital=1_000_000.0
    )
    # Sanity: fully invested after first rebalance (t=31), cash ~ 0.
    assert res.gross_exposure[35] > 0.9, "expected fully-invested book after first rebalance"
    assert abs(res.cash[35]) < 0.2 * 1_000_000.0
    # Correct engine: still fully invested after the SECOND rebalance (t=59).
    t_check = 62
    mv = float(np.sum(res.shares[t_check] * panel.close[t_check]))
    equity = float(res.equity_curve[t_check])
    invested_frac = mv / equity
    assert invested_frac >= 0.9, (
        f"SIZING DEFECT: after 2nd rebalance market value is {invested_frac:.1%} of "
        f"equity (should be ~100%). _weights_to_shares sized off cash "
        f"(~$0) instead of equity (~$1M) and liquidated the book."
    )
