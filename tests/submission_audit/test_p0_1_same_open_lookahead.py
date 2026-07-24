"""P0-1: SAME-OPEN fill of the close-t decision (declared timing violated).

Engine docstring & spec promise: decision_time="close", fill_time="next_open"
("trades at the NEXT OPEN after a close signal"). But run_portfolio_backtest
builds active_target[t] from target[t] (the signal ROW t, i.e. the close-t
decision, whose volatility feature consumes returns through close[t-1] and is
timestamped close[t]) and then fills it at open[t] — the SAME bar's open,
hours BEFORE the decision timestamp.

HAND CALC (daily calendar from 2022-01-01, lookbacks 10/2/5):
  - First nonzero target row is t0 = 31 (2022-02-01, first monthly rebalance
    with valid features).
  - CORRECT: decision at close[31] -> fill at open[32] -> shares[31] must still
    equal shares[30] (zero), first nonzero holdings at t=32.
  - ENGINE: shares[31] is already nonzero (filled at open[31]).

FIX SIGNATURE: in run_portfolio_backtest, shift execution one bar:
  active_target computed at t must be filled with fill_px = open_[t+1], i.e.
  trade loop uses `target_shares = _weights_to_shares(active_target[t-1], open_[t], ...)`
  (weights decided at close t-1 executed at open t), with rebal decisions taken
  from target[t-1], not target[t].
"""

import numpy as np
import pytest
from _audit_helpers import first_active_target_index, make_panel, small_spec

from app.strategy_lab.submission.engine import run_portfolio_backtest


@pytest.mark.xfail(strict=False, reason="P0-1: close-t decision filled at SAME open[t]; must fill open[t+1]")
def test_fill_occurs_next_open_not_same_open():
    T = 70
    a0 = 100.0 * np.exp(np.linspace(0.0, 0.4, T))   # rising -> long
    a1 = 100.0 * np.exp(np.linspace(0.0, -0.3, T))  # falling -> short
    panel = make_panel(np.column_stack([a0, a1]))
    res = run_portfolio_backtest(
        panel=panel, spec=small_spec(), strategy_hash="p0-1", initial_capital=1_000_000.0
    )
    t0 = first_active_target_index(res)  # decision row (close-t0 decision)
    # Correct next-open execution: holdings on the decision day are unchanged.
    assert np.allclose(res.shares[t0], res.shares[t0 - 1]), (
        f"LOOKAHEAD/TIMING DEFECT: shares changed at t={t0} (same bar as the "
        f"close-t decision). Decision at close[{t0}] must fill at open[{t0 + 1}]."
    )
    # And the position must exist by t0+1 (the strategy does trade).
    assert np.any(np.abs(res.shares[t0 + 1]) > 0)
