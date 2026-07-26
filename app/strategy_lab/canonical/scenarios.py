"""Typed synthetic-stress scenario generation (Phase 2.6 section 8).

Every mechanism produces a ``GeneratedScenario`` whose panel satisfies the
MarketDataPanel OHLCV invariants and preserves symbol identity. Mechanisms:

* drawdown            -> deterministic negative-return path over [start, start+duration)
* volatility_spike    -> additive return innovations (on returns, not levels)
* correlation_breakdown -> return-matrix transformation that preserves symbol
                           identity and emulates a target correlation structure

All randomness is seeded from ``ScenarioDefinition.seed`` for reproducibility.
Unknown mechanisms raise ``InvalidScenarioMechanismError`` (422, never 500).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import numpy as np

from app.strategy_lab.canonical.errors import InvalidScenarioMechanismError
from app.strategy_lab.submission.panels import MarketDataPanel


class ScenarioMechanism:
    DRAWDOWN = "drawdown"
    VOL_SPIKE = "vol_spike"
    CORRELATION_BREAKDOWN = "correlation_breakdown"
    ALL = (DRAWDOWN, VOL_SPIKE, CORRELATION_BREAKDOWN)


@dataclass(frozen=True)
class ScenarioDefinition:
    mechanism: str
    seed: int
    intensity: Decimal
    start_index: int
    duration: int
    parameters: dict[str, Decimal] = field(default_factory=dict)
    generator_version: str = "scenario-gen/1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism": self.mechanism,
            "seed": self.seed,
            "intensity": str(self.intensity),
            "start_index": self.start_index,
            "duration": self.duration,
            "parameters": {k: str(v) for k, v in self.parameters.items()},
            "generator_version": self.generator_version,
        }


@dataclass(frozen=True)
class GeneratedScenario:
    scenario_id: str
    definition: ScenarioDefinition
    panel: MarketDataPanel
    content_digest: str
    diagnostics: dict[str, Any]


# Supported scenario mechanisms (Phase 2.6 section 8). Unknown mechanisms must
# be rejected with a structured error, never silently skipped.
KNOWN_MECHANISMS: frozenset[str] = frozenset({
    "drawdown",
    "vol_spike",
    "correlation_breakdown",
})


def validate_mechanisms(mechanisms: list[str]) -> None:
    unknown = [m for m in mechanisms if m not in KNOWN_MECHANISMS]
    if unknown:
        raise InvalidScenarioMechanismError(f"unknown scenario mechanism(s): {', '.join(unknown)}")


def _panel_digest(panel: MarketDataPanel, definition: ScenarioDefinition) -> str:
    h = hashlib.sha256()
    h.update(definition.mechanism.encode())
    h.update(str(definition.seed).encode())
    h.update(str(float(definition.intensity)).encode())
    h.update(",".join(panel.assets).encode())
    h.update(np.ascontiguousarray(panel.open).tobytes())
    h.update(np.ascontiguousarray(panel.high).tobytes())
    h.update(np.ascontiguousarray(panel.low).tobytes())
    h.update(np.ascontiguousarray(panel.close).tobytes())
    h.update(np.ascontiguousarray(panel.volume).tobytes())
    if panel.benchmark_close is not None:
        h.update(np.ascontiguousarray(panel.benchmark_close).tobytes())
    h.update(",".join(d.isoformat() for d in panel.dates).encode())
    return h.hexdigest()


def _rebuild_ohlc(close: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Construct open/high/low from a NEW close path, preserving OHLCV invariants."""
    T, N = close.shape
    open_ = np.empty((T, N))
    open_[0] = close[0]
    open_[1:] = close[:-1]
    # intrabar range scales with the bar's own volatility (local, not global)
    bar_range = np.abs(np.diff(close, axis=0))
    bar_range = np.vstack([bar_range[0:1], bar_range])  # align to T
    high = np.maximum(open_, close) * (1.0 + 0.5 * bar_range / np.maximum(np.abs(close), 1e-9))
    low = np.minimum(open_, close) * (1.0 - 0.5 * bar_range / np.maximum(np.abs(close), 1e-9))
    high = np.maximum(high, np.maximum(open_, close))
    low = np.minimum(low, np.minimum(open_, close))
    low = np.maximum(low, 1e-6)
    high = np.maximum(high, low)
    return open_, high, low


def _returns(panel: MarketDataPanel) -> np.ndarray:
    return panel.close[1:] / panel.close[:-1] - 1.0


def generate_scenario(base: MarketDataPanel, definition: ScenarioDefinition) -> GeneratedScenario:
    mech = definition.mechanism
    if mech not in ScenarioMechanism.ALL:
        raise InvalidScenarioMechanismError(f"unknown scenario mechanism: {mech}")
    rng = np.random.default_rng(definition.seed)
    T, N = base.close.shape
    start = max(0, min(definition.start_index, T - 1))
    duration = max(1, min(definition.duration, T - start))
    end = start + duration
    intensity = float(definition.intensity)

    close = base.close.copy()
    diagnostics: dict[str, Any] = {"mechanism": mech, "intensity": intensity}

    if mech == ScenarioMechanism.DRAWDOWN:
        # Deterministic negative-return path over the declared interval.
        shock = -intensity
        per_bar = shock / duration
        factor = np.cumprod(np.full(duration, 1.0 + per_bar))
        close[start:end] = close[start:end] * factor[:, None]
        close[end:] = close[end:] * factor[-1]
        close = np.maximum(close, 1e-6)
        diagnostics["start_index"] = start
        diagnostics["duration"] = duration
        diagnostics["cumulative_shock"] = float(shock)
        diagnostics["per_bar_return_shock"] = float(per_bar)
        diagnostics["recovery_policy"] = "permanent level shift (no recovery)"

    elif mech == ScenarioMechanism.VOL_SPIKE:
        # Operate on returns, not raw close levels.
        rets = _returns(base)
        extra = rng.normal(0.0, intensity, size=(T - 1, N))
        extra[start - 1 : end - 1] += rng.normal(0.0, intensity, size=(duration, N))
        new_close = np.empty((T, N))
        new_close[0] = base.close[0]
        growth = (1.0 + rets) * (1.0 + extra)
        new_close[1:] = base.close[0] * np.cumprod(growth, axis=0)
        new_close = np.maximum(new_close, 1e-6)
        close = new_close
        pre_vol = float(np.std(rets))
        post_vol = float(np.std(rets[start - 1 : end - 1] + extra[start - 1 : end - 1]))
        diagnostics["pre_volatility"] = pre_vol
        diagnostics["post_volatility"] = post_vol

    elif mech == ScenarioMechanism.CORRELATION_BREAKDOWN:
        # Preserve symbol identity; transform the return matrix to emulate a
        # target correlation structure. A deterministic rotation of the return
        # columns (not a column permutation) changes cross-asset correlation
        # while keeping each column tied to its own asset.
        rets = _returns(base)
        theta = intensity * np.pi  # rotation angle derived from intensity
        c, s = np.cos(theta), np.sin(theta)
        rot = np.array([[c, -s], [s, c]])
        flat = rets.reshape(-1, N)
        # rotate in N//2 pairs; leftover single column untouched
        out = flat.copy()
        for k in range(0, N - (N % 2), 2):
            block = flat[:, k : k + 2] @ rot.T
            out[:, k : k + 2] = block
        new_close = np.empty((T, N))
        new_close[0] = base.close[0]
        new_close[1:] = base.close[0] * np.cumprod(1.0 + out, axis=0)
        new_close = np.maximum(new_close, 1e-6)
        close = new_close
        base_corr = np.corrcoef(rets.T)
        gen_corr = np.corrcoef(out.T)
        diagnostics["baseline_correlation_mean"] = float(np.mean(np.abs(base_corr - np.eye(N))))
        diagnostics["target_correlation"] = float(np.cos(theta))
        diagnostics["realized_correlation_mean"] = float(np.mean(np.abs(gen_corr - np.eye(N))))
        diagnostics["transformation_method"] = "deterministic_return_rotation"

    open_, high, low = _rebuild_ohlc(close)
    panel = MarketDataPanel(
        dates=base.dates,
        assets=base.assets,  # symbol identity preserved
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=base.volume,
        benchmark_close=base.benchmark_close,
        metadata=base.metadata,
        provenance=base.provenance,
    )
    scenario_id = str(uuid.uuid4())
    return GeneratedScenario(
        scenario_id=scenario_id,
        definition=definition,
        panel=panel,
        content_digest=_panel_digest(panel, definition),
        diagnostics=diagnostics,
    )


def assert_panel_invariants(panel: MarketDataPanel) -> None:
    """Raise ValueError unless every bar/asset satisfies the OHLCV invariants."""
    T, N = panel.T, panel.N
    open_ = panel.open
    high = panel.high
    low = panel.low
    close = panel.close
    for t in range(T):
        for j in range(N):
            o, h, lo, c = open_[t, j], high[t, j], low[t, j], close[t, j]
            if not (np.isfinite(o) and np.isfinite(h) and np.isfinite(lo) and np.isfinite(c)):
                raise ValueError(f"non-finite OHLC at ({t},{j})")
            if o <= 0 or c <= 0:
                raise ValueError(f"non-positive open/close at ({t},{j})")
            if not (lo <= min(o, c) <= h and h >= lo):
                raise ValueError(f"OHLC invariant violated at ({t},{j}): o={o} h={h} lo={lo} c={c}")


__all__ = [
    "ScenarioMechanism",
    "ScenarioDefinition",
    "GeneratedScenario",
    "generate_scenario",
    "assert_panel_invariants",
]
