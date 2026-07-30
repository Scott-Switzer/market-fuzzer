"""Portfolio constraint helpers (reset brief Phase 2 item 18).

Deterministic exposure math shared by executors. Keeps the gross/net/position-cap
logic in one audited place so no executor silently violates a declared limit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ExposureSplit:
    long_gross: float
    short_gross: float


def split_gross_net(gross: float, net: float) -> ExposureSplit:
    """Given declared gross G and net N, return per-side gross.

    long_gross = (G + N) / 2 ; short_gross = (G - N) / 2. Requires G >= |N|.
    """
    if gross < 0:
        raise ValueError("gross exposure must be >= 0")
    if gross + 1e-12 < abs(net):
        raise ValueError(f"infeasible exposure: gross {gross} < |net| {abs(net)}")
    return ExposureSplit(long_gross=(gross + net) / 2.0, short_gross=(gross - net) / 2.0)


def equal_weight_capped(
    selected: np.ndarray, side_gross: float, max_position: float
) -> tuple[np.ndarray, bool]:
    """Equal-weight the ``selected`` (boolean) names to ``side_gross`` total,
    capped at ``max_position`` per name.

    Returns (weights, feasible). When ``n * max_position < side_gross`` the side
    cannot reach the requested gross (infeasible): every name is set to
    ``max_position`` and feasible=False so the caller can emit a shortfall
    diagnostic rather than silently over-allocating.
    """
    n = int(selected.sum())
    w = np.zeros(selected.shape[0], dtype=float)
    if n == 0 or side_gross <= 0:
        return w, True
    per = side_gross / n
    feasible = True
    if per > max_position + 1e-12:
        per = max_position
        feasible = False
    w[selected] = per
    return w, feasible


def enforce_net_by_trim(weights: np.ndarray, target_net: float, max_position: float) -> np.ndarray:
    """Trim toward a net target symmetrically without exceeding position caps.

    Long weights are positive, short weights negative. This adjusts the larger
    side toward the net target; used only when rounding leaves a small residual.
    """
    net = float(np.sum(weights))
    diff = net - target_net
    if abs(diff) <= 1e-9:
        return weights
    pos = weights > 0
    neg = weights < 0
    out = weights.copy()
    if diff > 0 and pos.any():
        out[pos] -= diff / int(pos.sum())
    elif diff < 0 and neg.any():
        out[neg] -= diff / int(neg.sum())
    return np.clip(out, -max_position, max_position)


__all__ = [
    "ExposureSplit",
    "split_gross_net",
    "equal_weight_capped",
    "enforce_net_by_trim",
]
