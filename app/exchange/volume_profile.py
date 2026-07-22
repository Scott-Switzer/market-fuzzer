"""Intraday volume profile helpers shared by simulation and synthetic markets."""

from __future__ import annotations

import math
from typing import Literal


def u_shaped_intraday_volume_weights(steps: int) -> list[float]:
    """Deterministic U-shaped intraday volume weights that sum to 1.0.

    Open and close are heavier; midday is lighter. Used to scale per-step
    volume caps and displayed-depth autores.
    """
    if steps <= 0:
        return []
    if steps == 1:
        return [1.0]
    raw = [0.35 + 0.65 * (math.cos(math.pi * index / (steps - 1)) ** 2) for index in range(steps)]
    total = sum(raw)
    return [value / total for value in raw]


def flat_intraday_volume_weights(steps: int) -> list[float]:
    if steps <= 0:
        return []
    weight = 1.0 / steps
    return [weight] * steps


def intraday_volume_weights(profile: str, steps: int) -> list[float]:
    if profile == "u_shaped":
        return u_shaped_intraday_volume_weights(steps)
    return flat_intraday_volume_weights(steps)


def displayed_depth_autor(
    baseline_depth: int,
    liquidity_profile: Literal["deep", "normal", "thin"] = "normal",
    *,
    volume_weight: float = 1.0,
    intervention_multiplier: float = 1.0,
) -> int:
    """Scale displayed depth from liquidity profile and intraday volume weight.

    ``volume_weight`` should be centered near 1.0 (e.g. ``profile_weight * steps``).
    """
    profile_mult = {"deep": 1.35, "normal": 1.0, "thin": 0.55}.get(liquidity_profile, 1.0)
    volume_mult = max(0.25, min(2.5, float(volume_weight)))
    scaled = baseline_depth * profile_mult * volume_mult * max(0.05, float(intervention_multiplier))
    return max(1, int(round(scaled)))
