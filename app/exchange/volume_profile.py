"""Intraday volume profile helpers shared by simulation and synthetic markets."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

import numpy as np

REGIME_DEPTH_MULT = {
    "steady_trend": 1.0,
    "sideways_choppy": 0.95,
    "high_volatility": 0.7,
    "sudden_selloff": 0.45,
}


def u_shaped_intraday_volume_weights(
    steps: int,
    *,
    morning_session_bias: float = 1.0,
    afternoon_session_bias: float = 1.0,
    amplitude_jitter: float = 0.0,
    regime_mult: float = 1.0,
    seed: int | None = None,
) -> list[float]:
    """U-shaped intraday volume weights that sum to 1.0.

    Open and close are heavier; midday is lighter. Optional regime biases and
    amplitude jitter support stochastic intraday seasonality.
    ``regime_mult`` scales the U-shape amplitude before normalization so stressed
    regimes can exaggerate open/close volume spikes.
    """
    if steps <= 0:
        return []
    if steps == 1:
        return [1.0]
    rng = np.random.default_rng(seed) if seed is not None else None
    amp = 0.65
    if rng is not None and amplitude_jitter > 0:
        amp = float(np.clip(0.65 + rng.normal(0.0, amplitude_jitter), 0.35, 0.9))
    raw: list[float] = []
    for index in range(steps):
        base = 0.35 + amp * (math.cos(math.pi * index / (steps - 1)) ** 2)
        frac = index / (steps - 1)
        if frac < 0.5:
            base *= max(0.5, float(morning_session_bias))
        else:
            base *= max(0.5, float(afternoon_session_bias))
        # Regime multiplier exaggerates open/close spikes relative to midday.
        base *= max(0.1, float(regime_mult))
        raw.append(base)
    total = sum(raw) or 1.0
    return [value / total for value in raw]


def flat_intraday_volume_weights(steps: int) -> list[float]:
    if steps <= 0:
        return []
    weight = 1.0 / steps
    return [weight] * steps


def intraday_volume_weights(profile: str, steps: int, **kwargs: object) -> list[float]:
    if profile == "u_shaped":
        return u_shaped_intraday_volume_weights(steps, **kwargs)  # type: ignore[arg-type]
    return flat_intraday_volume_weights(steps)


def displayed_depth_autor(
    baseline_depth: int,
    liquidity_profile: Literal["deep", "normal", "thin"] = "normal",
    *,
    volume_weight: float = 1.0,
    intervention_multiplier: float = 1.0,
    abs_return: float = 0.0,
    eta: float = 12.0,
    regime_key: str | None = None,
) -> int:
    """Scale displayed depth from liquidity profile and intraday volume weight.

    ``volume_weight`` should be centered near 1.0 (e.g. ``profile_weight * steps``).
    Optional ``abs_return`` applies liquidity-crisis decay: depth * exp(-eta*|r|).
    """
    profile_mult = {"deep": 1.35, "normal": 1.0, "thin": 0.55}.get(liquidity_profile, 1.0)
    volume_mult = max(0.25, min(2.5, float(volume_weight)))
    regime_mult = REGIME_DEPTH_MULT.get(regime_key or "", 1.0)
    ret_mult = math.exp(-float(eta) * abs(float(abs_return)))
    scaled = (
        baseline_depth
        * profile_mult
        * volume_mult
        * regime_mult
        * ret_mult
        * max(0.05, float(intervention_multiplier))
    )
    return max(1, int(round(scaled)))


def depth_series(
    baseline_depth: int,
    returns: Sequence[float],
    *,
    liquidity_profile: Literal["deep", "normal", "thin"] = "normal",
    eta: float = 12.0,
    regime_key: str | None = None,
    volume_weights: Sequence[float] | None = None,
) -> list[int]:
    """Per-step displayed depth reacting to absolute returns and regime."""
    rets = list(returns)
    n = len(rets) + 1
    weights = list(volume_weights) if volume_weights is not None else [1.0] * n
    if len(weights) < n:
        weights = weights + [1.0] * (n - len(weights))
    out: list[int] = []
    for t in range(n):
        abs_r = abs(float(rets[t - 1])) if t > 0 and t - 1 < len(rets) else 0.0
        out.append(
            displayed_depth_autor(
                baseline_depth,
                liquidity_profile,
                volume_weight=float(weights[t]) * n,
                abs_return=abs_r,
                eta=eta,
                regime_key=regime_key,
            )
        )
    return out
