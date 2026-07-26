"""Momentum signals (reset brief Phase 2 item 18)."""

from __future__ import annotations

import numpy as np


def momentum_12_1(close: np.ndarray, *, long: int = 252, short: int = 21) -> np.ndarray:
    """12-1 style momentum: ``close[t-short]/close[t-long] - 1``.

    T x N -> T x N. NaN before ``long`` bars of history. No window shortening:
    a panel shorter than ``long`` bars simply has NaN rows (ineligible), never a
    silently altered lookback.
    """
    if close.ndim != 2:
        raise ValueError("close must be 2-D (T x N)")
    T, _ = close.shape
    out = np.full_like(close, np.nan, dtype=float)
    for t in range(long, T):
        prior = close[t - long]
        recent = close[t - short]
        with np.errstate(divide="ignore", invalid="ignore"):
            out[t] = np.where(prior > 0, recent / prior - 1.0, np.nan)
    return out


def total_return(close: np.ndarray, *, lookback: int) -> np.ndarray:
    """Simple ``close[t]/close[t-lookback] - 1`` total return. NaN before lookback."""
    if close.ndim != 2:
        raise ValueError("close must be 2-D (T x N)")
    T, _ = close.shape
    out = np.full_like(close, np.nan, dtype=float)
    for t in range(lookback, T):
        prior = close[t - lookback]
        with np.errstate(divide="ignore", invalid="ignore"):
            out[t] = np.where(prior > 0, close[t] / prior - 1.0, np.nan)
    return out


__all__ = ["momentum_12_1", "total_return"]
