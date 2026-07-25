"""P0-5: Borrow cost charged ONCE at short open, not accrued daily.

_charge_costs: `if qty < 0: borrow = borrow_bps/1e4 * notional / 252` — i.e.
exactly ONE day of borrow, only on the bar where short shares are ADDED. A
short held for months pays the same as one held overnight. (Bonus bug: qty<0
also fires on ordinary long SELLS, charging "borrow" for reducing a long.)

HAND CALC (borrow the ONLY nonzero cost, borrow_bps=365):
  First rebalance t=31 opens a short of ~0.5 * $1M = $500,000 notional.
  Short is held through t=69 => ~38 calendar days ~ 38 trading bars here.
  Correct daily accrual ~= 365/10_000 * 500_000 / 252 * 38 ~= $2,750.
  ENGINE charges one day at open (~$72), plus possibly one more on the second
  rebalance => < $200 total, an order of magnitude short.
  Test floor: >= 10 days of accrual = 10 * 365/1e4 * 500k * (1/252) ~= $724,
  using the realized short notional so the bound is exact.

FIX SIGNATURE: move borrow out of the per-trade loop into the daily loop:
  for each t: short_mv = sum(|shares[t,n]| * close[t,n] for shares[t,n] < 0);
  daily_borrow = spec.borrow_bps / 10_000.0 * short_mv / 252.0;
  cash[t] -= daily_borrow; borrow_total += daily_borrow.
Also restrict any open-trade borrow logic to trades that increase a NET SHORT
position, not every qty < 0.
"""

import numpy as np
import pytest
from _audit_helpers import first_active_target_index, make_panel, small_spec

from app.strategy_lab.submission.engine import run_portfolio_backtest


@pytest.mark.xfail(strict=False, reason="P0-5: borrow charged once at short open, not accrued daily")
def test_borrow_accrues_daily_on_held_short():
    T = 70
    a0 = 100.0 * np.exp(np.linspace(0.0, 0.4, T))
    a1 = 100.0 * np.exp(np.linspace(0.0, -0.3, T))
    panel = make_panel(np.column_stack([a0, a1]))
    spec = small_spec(borrow_bps=365.0)  # all other costs zero
    res = run_portfolio_backtest(panel=panel, spec=spec, strategy_hash="p0-5", initial_capital=1_000_000.0)
    t0 = first_active_target_index(res)
    # Realized short market value each day the short is held.
    short_mv = np.where(res.shares < 0, -res.shares, 0.0) * panel.close
    daily_short_mv = short_mv.sum(axis=1)
    days_short = int(np.sum(daily_short_mv > 0))
    assert days_short >= 20, f"short only held {days_short} bars; fixture broken"
    # Floor: at least 10 days of correct accrual on the average short book.
    avg_short = float(daily_short_mv[daily_short_mv > 0].mean())
    ten_day_floor = 365.0 / 10_000.0 * avg_short / 252.0 * 10.0
    assert res.cost_summary["borrow"] >= ten_day_floor, (
        f"BORROW DEFECT: short held {days_short} bars (avg ${avg_short:,.0f}) but "
        f"borrow charged = ${res.cost_summary['borrow']:,.2f} < 10-day floor "
        f"${ten_day_floor:,.2f}. Borrow is charged once at open (line ~380), "
        f"not accrued daily. (t0={t0})"
    )
