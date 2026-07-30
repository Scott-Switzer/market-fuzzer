"""Signal reference tests with hand-calculated values (reset brief item 28)."""

from __future__ import annotations

import math
from datetime import date

import numpy as np

from app.strategies.schedules import decision_mask
from app.strategies.signals import (
    average_rank,
    momentum_12_1,
    realized_volatility,
    simple_moving_average,
    total_return,
)


def test_momentum_formula_hand_calc():
    # close[t-skip]/close[t-long]-1 ; long=3, skip=1
    close = np.array([[10.0], [11.0], [12.0], [13.0], [15.0]])  # T=5, N=1
    out = momentum_12_1(close, long=3, short=1)
    # first valid at t=3: close[2]/close[0]-1 = 12/10-1 = 0.2
    assert math.isnan(out[0, 0]) and math.isnan(out[2, 0])
    assert abs(out[3, 0] - 0.2) < 1e-12
    # t=4: close[3]/close[1]-1 = 13/11-1
    assert abs(out[4, 0] - (13 / 11 - 1)) < 1e-12


def test_momentum_no_window_shortening_short_history():
    close = np.array([[10.0], [11.0], [12.0]])  # only 3 bars, long=252
    out = momentum_12_1(close, long=252, short=21)
    assert np.all(np.isnan(out))  # ineligible, not silently shortened


def test_increasing_prices_positive_momentum():
    close = (np.arange(1, 301) ** 1.0).reshape(-1, 1) + 100.0
    out = momentum_12_1(close, long=252, short=21)
    valid = out[~np.isnan(out)]
    assert np.all(valid > 0)


def test_constant_prices_zero_momentum():
    close = np.full((300, 1), 50.0)
    out = momentum_12_1(close, long=252, short=21)
    valid = out[~np.isnan(out)]
    assert np.allclose(valid, 0.0)


def test_realized_vol_hand_calc():
    # returns known; window=2
    close = np.array([[100.0], [110.0], [99.0], [108.9]])
    # r1=0.1, r2=-0.1, r3=0.1
    out = realized_volatility(close, window=2)
    # t=2 uses returns[1..2] = [0.1,-0.1], std ddof=1 = sqrt(((0.1-0)^2+(-0.1-0)^2)/1)=sqrt(0.02)
    expected = math.sqrt(0.02) * math.sqrt(252.0)
    assert abs(out[2, 0] - expected) < 1e-9


def test_sma_hand_calc():
    close = np.array([[1.0], [2.0], [3.0], [4.0], [5.0]])
    out = simple_moving_average(close, window=3)
    assert math.isnan(out[1, 0])
    assert out[2, 0] == 2.0  # mean(1,2,3)
    assert out[4, 0] == 4.0  # mean(3,4,5)


def test_total_return_hand_calc():
    close = np.array([[10.0], [11.0], [12.0], [13.2]])
    out = total_return(close, lookback=2)
    assert out[2, 0] == 12 / 10 - 1
    assert abs(out[3, 0] - (13.2 / 11 - 1)) < 1e-12


def test_average_rank_ties_order_independent():
    a = np.array([3.0, 1.0, 2.0, 2.0])
    b = np.array([2.0, 2.0, 1.0, 3.0])  # permuted values
    ra = average_rank(a)
    rb = average_rank(b)
    # the two tied 2.0s in `a` share the average rank
    assert ra[2] == ra[3]
    # ranking is a pure function of values, not position: same multiset -> same rank set
    assert sorted(ra.tolist()) == sorted(rb.tolist())


def test_average_rank_ignores_nan():
    x = np.array([np.nan, 1.0, 2.0, np.nan, 3.0])
    r = average_rank(x)
    assert math.isnan(r[0]) and math.isnan(r[3])
    assert r[1] < r[2] < r[4]


def test_decision_mask_month_end():
    dates = [date(2020, 1, 1) + __import__("datetime").timedelta(days=i) for i in range(90)]
    m = decision_mask(dates, "monthly")
    idx = [i for i, x in enumerate(m) if x]
    assert [dates[i].isoformat() for i in idx] == ["2020-01-31", "2020-02-29", "2020-03-30"]


def test_decision_mask_year_end():
    from datetime import timedelta

    dates = [date(2020, 12, 28) + timedelta(days=i) for i in range(10)]
    m = decision_mask(dates, "monthly")
    idx = [i for i, x in enumerate(m) if x]
    # Dec 2020 last available bar (Dec 31) and the final bar (Jan 6)
    got = {dates[i].isoformat() for i in idx}
    assert "2020-12-31" in got


def test_decision_mask_weekly_holiday_short_week():
    from datetime import timedelta

    # skip a Wednesday to simulate a holiday
    base = [date(2021, 1, 4) + timedelta(days=i) for i in range(14)]
    dates = [d for d in base if d.weekday() < 5 and d != date(2021, 1, 6)]
    m = decision_mask(dates, "weekly")
    idx = [i for i, x in enumerate(m) if x]
    # last bar of ISO week 1 present (Jan 8 Fri), and final bar
    assert m[-1]  # final bar always a decision bar
    assert len(idx) >= 2


def test_decision_mask_daily_all_true():
    from datetime import timedelta

    dates = [date(2021, 1, 1) + timedelta(days=i) for i in range(5)]
    assert decision_mask(dates, "daily").all()
