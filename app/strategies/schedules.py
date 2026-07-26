"""Shared rebalance scheduling for all executors (reset brief Phase 2 item 17).

ONE scheduling implementation, used by every executor. The rule is uniform:

* DECISION happens at the CLOSE of the final valid trading bar of the period
  (month/week), or the current close (daily).
* EXECUTION happens at the NEXT valid open (handled by the accounting engine).
* The final decision in a dataset may remain unexecuted (no next open).

``decision_mask[t] is True`` means "bar t is a decision bar": an executor
computes target weights using data available at close[t]; the accounting engine
fills them at open[t+1].

We deliberately do NOT use a "first trading day of the month" mask (the legacy
behavior) as the monthly decision schedule -- that decides on the first bar with
stale month-open data. Month-end decision + next-open fill is the correct,
point-in-time-safe convention.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import numpy as np

Frequency = str  # "daily" | "weekly" | "monthly"


def _iso_year_week(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return (iso[0], iso[1])


def decision_mask(dates: Sequence[date], frequency: Frequency) -> np.ndarray:
    """Return a boolean length-T mask marking decision bars.

    monthly: last valid bar of each calendar month.
    weekly:  last valid bar of each ISO week.
    daily:   every bar.
    """
    T = len(dates)
    mask = np.zeros(T, dtype=bool)
    if T == 0:
        return mask

    freq = frequency.lower()
    if freq == "daily":
        mask[:] = True
        return mask

    if freq == "monthly":
        key = [(d.year, d.month) for d in dates]
    elif freq == "weekly":
        key = [_iso_year_week(d) for d in dates]
    else:
        raise ValueError(f"unsupported frequency: {frequency!r}")

    # A bar is a decision bar if it is the LAST bar whose period key equals its
    # own (i.e. the next bar starts a new period, or it is the final bar).
    for t in range(T):
        is_last_in_period = (t == T - 1) or (key[t + 1] != key[t])
        if is_last_in_period:
            mask[t] = True
    return mask


__all__ = ["decision_mask", "Frequency"]
