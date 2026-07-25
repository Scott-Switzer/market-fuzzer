"""Conditional performance attribution helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np


def attribution_by_regime(
    prices: Sequence[float],
    positions: Sequence[float],
    regimes: Sequence[str],
) -> dict[str, dict[str, float]]:
    """Attribute strategy PnL / return by regime label."""
    px = np.asarray(prices, dtype=float)
    pos = np.asarray(positions, dtype=float)
    labels = list(regimes)
    if px.size < 2:
        return {}
    rets = np.diff(px) / np.clip(px[:-1], 1e-9, None)
    n = min(len(rets), len(pos) - 1 if len(pos) else 0, len(labels))
    out: dict[str, dict[str, float]] = {}
    for i in range(n):
        lab = str(labels[i])
        bucket = out.setdefault(lab, {"pnl": 0.0, "bars": 0.0, "return_sum": 0.0})
        pnl = float(pos[i]) * float(rets[i])
        bucket["pnl"] += pnl
        bucket["return_sum"] += float(rets[i]) * float(pos[i])
        bucket["bars"] += 1.0
    for bucket in out.values():
        bars = max(bucket["bars"], 1.0)
        bucket["avg_return"] = round(bucket["return_sum"] / bars, 8)
        bucket["pnl"] = round(bucket["pnl"], 8)
    return out


def conditional_performance_attribution(
    pnl_grid: Mapping[str, Sequence[float]] | np.ndarray,
    factors: Mapping[str, Sequence[float]] | None = None,
) -> dict[str, object]:
    """Simple factor-neutral attribution via OLS of PnL on factor returns."""
    if isinstance(pnl_grid, Mapping):
        names = list(pnl_grid.keys())
        mat = np.column_stack([np.asarray(pnl_grid[n], dtype=float) for n in names])
        # Aggregate to a single series if multiple strategies provided.
        y = mat.mean(axis=1)
    else:
        y = np.asarray(pnl_grid, dtype=float).reshape(-1)
        names = ["strategy"]
    if factors is None or not factors:
        return {
            "alpha": round(float(np.mean(y)), 8),
            "factor_betas": {},
            "r_squared": 0.0,
            "strategies": names,
        }
    factor_names = list(factors.keys())
    x = np.column_stack([np.asarray(factors[k], dtype=float) for k in factor_names])
    n = min(len(y), len(x))
    y = y[:n]
    x = x[:n]
    x_design = np.column_stack([np.ones(n), x])
    try:
        coef, *_ = np.linalg.lstsq(x_design, y, rcond=None)
    except np.linalg.LinAlgError:
        coef = np.zeros(x_design.shape[1])
    fitted = x_design @ coef
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2)) or 1.0
    betas = {factor_names[i]: round(float(coef[i + 1]), 8) for i in range(len(factor_names))}
    return {
        "alpha": round(float(coef[0]), 8),
        "factor_betas": betas,
        "r_squared": round(1.0 - ss_res / ss_tot, 6),
        "strategies": names,
    }
