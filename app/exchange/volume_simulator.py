"""Regime-conditional clustered volume simulation (Hawkes/ACD-style)."""

from __future__ import annotations

import math
from typing import Literal

import numpy as np

REGIME_VOLUME_MULTIPLIER = {
    "steady_trend": 1.0,
    "sideways_choppy": 1.15,
    "high_volatility": 1.6,
    "sudden_selloff": 2.2,
}

LIQUIDITY_BASELINE = {
    "deep": 1.4e6,
    "normal": 1.0e6,
    "thin": 4.5e5,
}


class VolumeSimulator:
    """Generate autocorrelated, regime-scaled volume alongside returns.

    Intraday/seasonal clustering is modeled with U-shape open/close boosts and
    a return-driven multiplier::

        V_t = baseline * regime_mult * (1 + kappa * |r_t|)
              * r_mult * u_mult
              * (1 + excitation) * lognormal_shock

    ``u_params`` controls the intraday U-shape: ``(open_mult, close_mult)``.
    ``r_mult`` is a return-driven scaling factor (e.g. ``1 + gamma * |r|``).
    """

    def __init__(
        self,
        *,
        alpha: float = 0.35,
        beta: float = 0.55,
        kappa: float = 8.0,
        liquidity_profile: Literal["deep", "normal", "thin"] = "normal",
        u_params: tuple[float, float] = (1.35, 1.25),
        r_mult_enabled: bool = True,
        r_mult_gamma: float = 6.0,
    ) -> None:
        self.alpha = float(alpha)
        self.beta = float(beta)
        self.kappa = float(kappa)
        self.liquidity_profile = liquidity_profile
        self.u_params = u_params
        self.r_mult_enabled = bool(r_mult_enabled)
        self.r_mult_gamma = float(r_mult_gamma)

    def generate(
        self,
        regime_key: str,
        returns: np.ndarray,
        seed: int,
        *,
        length: int | None = None,
        u_params: tuple[float, float] | None = None,
        r_mult: bool | None = None,
        r_gamma: float | None = None,
    ) -> np.ndarray:
        rng = np.random.default_rng(int(seed) % (2**31 - 1))
        rets = np.asarray(returns, dtype=float)
        n = int(length) if length is not None else max(1, int(rets.size) + 1)
        if rets.size < n - 1:
            pad = np.zeros(max(0, n - 1 - rets.size), dtype=float)
            rets = np.concatenate([rets, pad])
        elif rets.size > n - 1:
            rets = rets[: n - 1]

        baseline = LIQUIDITY_BASELINE.get(self.liquidity_profile, LIQUIDITY_BASELINE["normal"])
        regime_mult = REGIME_VOLUME_MULTIPLIER.get(regime_key, 1.0)
        # Resolve per-call overrides or fall back to constructor defaults.
        open_mult, close_mult = (
            (float(u_params[0]), float(u_params[1]))
            if u_params is not None
            else (float(self.u_params[0]), float(self.u_params[1]))
        )
        r_mult_active = bool(self.r_mult_enabled) if r_mult is None else bool(r_mult)
        r_gamma = float(self.r_mult_gamma if r_gamma is None else r_gamma)

        volumes = np.empty(n, dtype=float)
        excitation = 0.0
        decay = math.exp(-self.beta)

        def _u_shape_mult(index: int) -> float:
            if n <= 1:
                return 1.0
            fraction = index / max(n - 1, 1)
            # U-shape: strongest at open, taper to midday, rise again into close.
            if fraction < 0.5:
                return 1.0 + (open_mult - 1.0) * (1.0 - 2.0 * fraction)
            return 1.0 + (close_mult - 1.0) * (2.0 * fraction - 1.0)

        abs_ret0 = abs(float(rets[0])) if rets.size else 0.0
        u_mult0 = max(0.25, _u_shape_mult(0))
        r_mult0 = max(0.25, 1.0 + r_gamma * abs_ret0) if r_mult_active else 1.0
        volumes[0] = max(
            1.0,
            baseline
            * regime_mult
            * (1.0 + self.kappa * abs_ret0)
            * u_mult0
            * r_mult0
            * float(rng.lognormal(0.0, 0.08)),
        )
        for t in range(1, n):
            r = abs(float(rets[t - 1])) if t - 1 < rets.size else 0.0
            u_mult = max(0.25, _u_shape_mult(t))
            r_mult_val = max(0.25, 1.0 + r_gamma * r) if r_mult_active else 1.0
            excitation = self.alpha * excitation * decay + self.alpha * (volumes[t - 1] / baseline)
            shock = float(rng.lognormal(0.0, 0.12))
            volumes[t] = max(
                1.0,
                baseline
                * regime_mult
                * (1.0 + self.kappa * r)
                * u_mult
                * r_mult_val
                * (1.0 + excitation)
                * shock,
            )
        return volumes
