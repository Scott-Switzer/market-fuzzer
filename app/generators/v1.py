"""Interpretable, deterministic synthetic-world generator ensemble.

These generators produce immutable event streams and disclose their assumptions.
They are deliberately not neural models and do not use historical rows as paths.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class WorldEventV1:
    exchange_time_ns: int
    instrument_id: str
    kind: str
    side: str | None
    price_ticks: int
    quantity: int
    regime: str

    def __post_init__(self) -> None:
        if self.exchange_time_ns < 0 or self.price_ticks <= 0 or self.quantity <= 0:
            raise ValueError("world events require non-negative time and positive price/quantity")


@dataclass(frozen=True, slots=True)
class GeneratedWorldV1:
    family_id: str
    generator_version: str
    seed: int
    events: tuple[WorldEventV1, ...]
    assumptions: tuple[str, ...]
    parameters: dict[str, float | int | str]
    stylized_fact_diagnostics: dict[str, float | int]
    supported_claims: tuple[str, ...]
    limitations: tuple[str, ...]

    @property
    def digest(self) -> str:
        value = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), default=list).encode()
        return hashlib.sha256(value).hexdigest()


class WorldGeneratorV1(Protocol):
    family_id: str
    generator_version: str

    def generate(self, *, seed: int, instruments: tuple[str, ...], steps: int) -> GeneratedWorldV1: ...


def _diagnostics(events: tuple[WorldEventV1, ...]) -> dict[str, float | int]:
    buys = sum(event.quantity for event in events if event.side == "buy")
    sells = sum(event.quantity for event in events if event.side == "sell")
    times = [event.exchange_time_ns for event in events]
    gaps = [right - left for left, right in zip(times, times[1:], strict=False)]
    prices = [event.price_ticks for event in events]
    returns = [math.log(right / left) for left, right in zip(prices, prices[1:], strict=False) if left]
    return {
        "event_count": len(events),
        "signed_volume_imbalance": round((buys - sells) / max(1, buys + sells), 8),
        "mean_interarrival_ns": round(sum(gaps) / len(gaps), 3) if gaps else 0.0,
        "return_abs_mean": round(sum(abs(value) for value in returns) / len(returns), 10) if returns else 0.0,
        "unique_regime_count": len({event.regime for event in events}),
    }


class HeterogeneousAgentGeneratorV1:
    family_id = "heterogeneous_agent_v1"
    generator_version = "1.0.0"

    def generate(self, *, seed: int, instruments: tuple[str, ...], steps: int) -> GeneratedWorldV1:
        if not instruments or steps <= 0:
            raise ValueError("instruments and positive steps are required")
        rng, prices = random.Random(seed), {instrument: 10_000 for instrument in instruments}
        events: list[WorldEventV1] = []
        for step in range(steps):
            regime = "shock" if step == steps // 2 else "normal"
            for instrument in instruments:
                fundamental_signal = rng.gauss(0, 1.4) + (-5.0 if regime == "shock" else 0.0)
                liquidity_signal = rng.gauss(0, 1.0)
                informed = "buy" if fundamental_signal > 0 else "sell"
                noise = "buy" if rng.random() < 0.5 else "sell"
                side = informed if rng.random() < 0.58 else noise
                prices[instrument] = max(1, prices[instrument] + round(fundamental_signal + liquidity_signal))
                events.append(
                    WorldEventV1(
                        step * 1_000_000 + len(events),
                        instrument,
                        "agent_order",
                        side,
                        prices[instrument],
                        10 * (1 + rng.randrange(1, 8)),
                        regime,
                    )
                )
        stream = tuple(events)
        return GeneratedWorldV1(
            self.family_id,
            self.generator_version,
            seed,
            stream,
            ("Fundamental, liquidity, informed/noise, and shock agents are explicitly parameterized.",),
            {"informed_probability": 0.58, "shock_step": steps // 2},
            _diagnostics(stream),
            ("Tests strategy response to declared heterogeneous-agent mechanisms.",),
            ("Not calibrated to a specific venue or participant population.",),
        )


class RegimeSwitchingPointProcessGeneratorV1:
    family_id = "regime_switching_point_process_v1"
    generator_version = "1.0.0"

    def generate(self, *, seed: int, instruments: tuple[str, ...], steps: int) -> GeneratedWorldV1:
        if not instruments or steps <= 0:
            raise ValueError("instruments and positive steps are required")
        rng, prices = random.Random(seed), {instrument: 10_000 for instrument in instruments}
        events: list[WorldEventV1] = []
        regimes = (("quiet", 0.35, 0.50), ("stressed", 0.82, 0.67), ("recovery", 0.48, 0.55))
        for step in range(steps):
            regime, intensity, persistence = regimes[min(2, step * 3 // steps)]
            for instrument in instruments:
                if rng.random() > intensity:
                    continue
                previous = events[-1].side if events and events[-1].instrument_id == instrument else None
                side = (
                    previous
                    if previous and rng.random() < persistence
                    else ("buy" if rng.random() < 0.5 else "sell")
                )
                prices[instrument] = max(
                    1, prices[instrument] + (1 if side == "buy" else -1) * rng.randrange(1, 4)
                )
                events.append(
                    WorldEventV1(
                        step * 1_000_000 + len(events),
                        instrument,
                        "marked_arrival",
                        side,
                        prices[instrument],
                        10 * rng.randrange(1, 12),
                        regime,
                    )
                )
        stream = tuple(events)
        return GeneratedWorldV1(
            self.family_id,
            self.generator_version,
            seed,
            stream,
            ("Arrival intensity, side persistence, size, and regime schedule are separate inputs.",),
            {"quiet_intensity": 0.35, "stressed_intensity": 0.82, "side_persistence": 0.67},
            _diagnostics(stream),
            ("Tests sensitivity to declared event-flow regimes and clustered arrivals.",),
            (
                "A simple marked process does not establish queue-position or venue-specific cancellation realism.",
            ),
        )


class CorrelatedLatentFactorGeneratorV1:
    family_id = "correlated_latent_factor_v1"
    generator_version = "1.0.0"

    def generate(self, *, seed: int, instruments: tuple[str, ...], steps: int) -> GeneratedWorldV1:
        if len(instruments) < 2 or steps <= 0:
            raise ValueError("at least two instruments and positive steps are required")
        rng, prices = (
            random.Random(seed),
            {instrument: 10_000 + 100 * index for index, instrument in enumerate(instruments)},
        )
        events: list[WorldEventV1] = []
        for step in range(steps):
            regime = "structural_break" if step >= steps * 2 // 3 else "factor_normal"
            factor = rng.gauss(-2.5 if regime == "structural_break" else 0.0, 1.6)
            for index, instrument in enumerate(instruments):
                beta, residual = 0.65 + 0.1 * index, rng.gauss(0, 1.1 + 0.15 * index)
                move = round(beta * factor + residual)
                prices[instrument] = max(1, prices[instrument] + move)
                side = "buy" if move >= 0 else "sell"
                events.append(
                    WorldEventV1(
                        step * 1_000_000 + len(events),
                        instrument,
                        "latent_factor_update",
                        side,
                        prices[instrument],
                        10 * rng.randrange(2, 10),
                        regime,
                    )
                )
        stream = tuple(events)
        return GeneratedWorldV1(
            self.family_id,
            self.generator_version,
            seed,
            stream,
            (
                "A common latent factor, idiosyncratic residuals, and structural-break schedule determine cross-asset moves.",
            ),
            {"base_beta": 0.65, "structural_break_step": steps * 2 // 3, "factor_volatility": 1.6},
            _diagnostics(stream),
            ("Tests cross-asset dependence and declared structural-break sensitivity.",),
            ("Latent factors are model assumptions, not inferred economic truth or a volatility surface.",),
        )
