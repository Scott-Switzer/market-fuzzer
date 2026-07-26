"""Realized-volatility signal (reset brief Phase 2 item 18)."""

from __future__ import annotations

import math

import numpy as np


def realized_volatility(close: np.ndarray, *, window: int = 63) -> np.ndarray:
    """Annualized realized vol from daily simple returns.

    ``r[t] = close[t]/close[t-1] - 1`` and
    ``vol[t] = std(r[t-window+1 .. t], ddof=1) * sqrt(252)``.

    The window is the ``window`` returns ending at (and INCLUDING) return t.
    NaN before enough history; no window shortening.
    """
    if close.ndim != 2:
        raise ValueError("close must be 2-D (T x N)")
    T, _ = close.shape
    returns = np.full_like(close, np.nan, dtype=float)
    returns[1:] = close[1:] / close[:-1] - 1.0
    out = np.full_like(close, np.nan, dtype=float)
    for t in range(window, T):
        block = returns[t - window + 1 : t + 1]  # `window` rows
        if np.any(~np.isfinite(block)):
            continue
        out[t] = np.std(block, axis=0, ddof=1) * math.sqrt(252.0)
    return out


__all__ = ["realized_volatility"]
