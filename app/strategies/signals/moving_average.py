"""Moving-average signals + deterministic ranking (reset brief Phase 2 items 18, 20)."""

from __future__ import annotations

import numpy as np


def simple_moving_average(close: np.ndarray, *, window: int) -> np.ndarray:
    """Trailing SMA of close over ``window`` bars (inclusive of t). NaN before warmup."""
    if close.ndim != 2:
        raise ValueError("close must be 2-D (T x N)")
    if window < 1:
        raise ValueError("window must be >= 1")
    T, _ = close.shape
    out = np.full_like(close, np.nan, dtype=float)
    for t in range(window - 1, T):
        out[t] = np.mean(close[t - window + 1 : t + 1], axis=0)
    return out


def average_rank(x: np.ndarray) -> np.ndarray:
    """Deterministic percentile rank in (0, 1] with AVERAGE ranks for ties.

    Tie outcome does not depend on input column order. NaN entries stay NaN and
    are excluded from the ranking population.
    """
    out = np.full_like(x, np.nan, dtype=float)
    valid = np.isfinite(x)
    m = int(valid.sum())
    if m < 1:
        return out
    vals = x[valid]
    order = np.argsort(vals, kind="mergesort")
    sorted_vals = vals[order]
    ranks = np.empty(m, dtype=float)
    i = 0
    while i < m:
        j = i
        while j + 1 < m and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based average rank
        ranks[order[i : j + 1]] = avg
        i = j + 1
    out[valid] = ranks / m
    return out


__all__ = ["simple_moving_average", "average_rank"]
