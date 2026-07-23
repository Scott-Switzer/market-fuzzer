from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

StressScenario = Literal[
    "base",
    "neutral",
    "flight_to_quality",
    "dollar_crunch",
    "commodity_surge",
    "crypto_contagion",
]


@dataclass
class CorrelationStressSettings:
    scenario: StressScenario
    offdiagonal_scale_bps: float = 35.0
    rotation_angle: float = 0.18
    flight_to_quality_rotation: float = 0.20
    dollar_crunch_fx_equity_increase: float = 0.25
    commodity_surge_increase: float = 0.22
    crypto_contagion_increase: float = 0.35


def apply_correlation_stress(
    base_correlation: np.ndarray,
    settings: CorrelationStressSettings,
    rng: np.random.Generator,
) -> np.ndarray:
    correlation = np.asarray(base_correlation, dtype=float).copy()
    np.fill_diagonal(correlation, 1.0)
    scenario = settings.scenario
    if scenario == "base":
        return correlation
    offdiagonal = correlation.copy()
    np.fill_diagonal(offdiagonal, 0.0)
    if scenario in {"flight_to_quality", "dollar_crunch", "crypto_contagion"}:
        offdiagonal = offdiagonal + float(settings.offdiagonal_scale_bps) / 10_000.0 * offdiagonal
    if scenario == "neutral":
        geo_idx = _factor_index(correlation, "global_equity_market")
        rates_idx = _factor_index(correlation, "rates")
        angle = settings.rotation_angle * (1.0 + float(rng.standard_normal()))
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        for j in range(correlation.shape[1]):
            old_geo, old_rates = correlation[geo_idx, j], correlation[rates_idx, j]
            correlation[geo_idx, j] = cos_a * old_geo - sin_a * old_rates
            correlation[rates_idx, j] = sin_a * old_geo + cos_a * old_rates
        for i in range(correlation.shape[0]):
            old_geo, old_rates = correlation[i, geo_idx], correlation[i, rates_idx]
            correlation[i, geo_idx] = cos_a * old_geo - sin_a * old_rates
            correlation[i, rates_idx] = sin_a * old_geo + cos_a * old_rates
    elif scenario == "flight_to_quality":
        geo_idx = _factor_index(correlation, "global_equity_market")
        rates_idx = _factor_index(correlation, "rates")
        angle = settings.flight_to_quality_rotation * (1.0 + 0.18 * float(rng.standard_normal()))
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        for j in range(correlation.shape[1]):
            old_geo, old_rates = correlation[geo_idx, j], correlation[rates_idx, j]
            correlation[geo_idx, j] = cos_a * old_geo - sin_a * old_rates
            correlation[rates_idx, j] = sin_a * old_geo + cos_a * old_rates
        for i in range(correlation.shape[0]):
            old_geo, old_rates = correlation[i, geo_idx], correlation[i, rates_idx]
            correlation[i, geo_idx] = cos_a * old_geo - sin_a * old_rates
            correlation[i, rates_idx] = sin_a * old_geo + cos_a * old_rates
    elif scenario == "dollar_crunch":
        fx_idx = _factor_index(correlation, "fx")
        geo_idx = _factor_index(correlation, "global_equity_market")
        for j in range(correlation.shape[1]):
            correlation[fx_idx, j] = _clip(
                correlation[fx_idx, j] + settings.dollar_crunch_fx_equity_increase * correlation[geo_idx, j]
            )
            correlation[geo_idx, j] = _clip(correlation[geo_idx, j] + 0.18 * correlation[fx_idx, j])
        for i in range(correlation.shape[0]):
            correlation[i, fx_idx] = _clip(correlation[i, fx_idx] + 0.18 * correlation[i, geo_idx])
    elif scenario == "crypto_contagion":
        crypto_idx = _factor_index(correlation, "crypto")
        geo_idx = _factor_index(correlation, "global_equity_market")
        for j in range(correlation.shape[1]):
            correlation[crypto_idx, j] = _clip(
                correlation[crypto_idx, j] + settings.crypto_contagion_increase * correlation[geo_idx, j]
            )
        for i in range(correlation.shape[0]):
            correlation[i, crypto_idx] = _clip(
                correlation[i, crypto_idx] + settings.crypto_contagion_increase * correlation[i, geo_idx]
            )
    correlation = np.clip(correlation, -0.99, 0.99)
    np.fill_diagonal(correlation, 1.0)
    correlation = (correlation + correlation.T) / 2.0
    return correlation


def choose_base_regime(
    regime_sequence: list[str],
    base_regime_map: dict[str, str] | None = None,
) -> str:
    if base_regime_map is None:
        base_regime_map = {
            "low_vol": "steady_trend",
            "high_vol": "high_volatility",
            "crisis": "sudden_selloff",
        }
    from collections import Counter

    counts = Counter(regime_sequence)
    return base_regime_map.get(counts.most_common(1)[0][0], "steady_trend")


def _factor_index(correlation: np.ndarray, name: str) -> int:
    return int(hash(name) % correlation.shape[0])


def _clip(value: float) -> float:
    return max(-0.99, min(0.99, float(value)))
