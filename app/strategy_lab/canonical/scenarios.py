"""Typed synthetic-stress scenario generation (Phase 2.6.1 semantic closure).

Every mechanism produces a ``GeneratedScenario`` whose panel satisfies the
MarketDataPanel OHLCV invariants and preserves symbol identity. Mechanisms:

* drawdown            -> deterministic negative-return path over [start, start+duration)
* vol_spike           -> additive return innovations INSIDE the declared window only
* correlation_breakdown -> return-matrix rotation applied INSIDE the declared
                           window only, preserving symbol identity

All randomness is seeded from ``ScenarioDefinition.seed`` for reproducibility.
Unknown mechanisms raise ``InvalidScenarioMechanismError`` (422, never 500).

Interval contract (Phase 2.6.1 gate 10): bars strictly BEFORE ``start_index``
are numerically identical to the base panel's close for every mechanism. Bars
after the window may differ only through the compounding of in-window return
changes (levels chain), never through fresh perturbation.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import numpy as np

from app.market_data.adjustments import AdjustmentPolicy
from app.market_data.calendar import CalendarPolicy
from app.market_data.contracts import AssetType, EligibilitySource, InstrumentIdentifier
from app.market_data.panel import MarketDataPanel
from app.strategy_lab.canonical.errors import InvalidScenarioMechanismError


class ScenarioMechanism:
    DRAWDOWN = "drawdown"
    VOL_SPIKE = "vol_spike"
    CORRELATION_BREAKDOWN = "correlation_breakdown"
    ALL = (DRAWDOWN, VOL_SPIKE, CORRELATION_BREAKDOWN)


def stable_seed(*parts: Any) -> int:
    """Derive a reproducible 32-bit seed from arbitrary parts via SHA-256.

    NEVER use Python's builtin ``hash`` for seeds: it is randomized per process
    (PYTHONHASHSEED), so restarts would generate different scenarios.
    """
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8"))
    return int.from_bytes(h.digest()[:4], "big")


@dataclass(frozen=True)
class ScenarioDefinition:
    mechanism: str
    seed: int
    intensity: Decimal
    start_index: int
    duration: int
    parameters: dict[str, Decimal] = field(default_factory=dict)
    generator_version: str = "scenario-gen/1.1"

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
KNOWN_MECHANISMS: frozenset[str] = frozenset(
    {
        "drawdown",
        "vol_spike",
        "correlation_breakdown",
    }
)


def validate_mechanisms(mechanisms: list[str]) -> None:
    unknown = [m for m in mechanisms if m not in KNOWN_MECHANISMS]
    if unknown:
        raise InvalidScenarioMechanismError(f"unknown scenario mechanism(s): {', '.join(unknown)}")


def _panel_content_digest(panel: MarketDataPanel) -> str:
    """Digest of the GENERATED market world's actual content (OHLCV + dates).

    Critically, this is derived ONLY from the realized panel tensors and the
    calendar -- NEVER from the scenario ``seed``. Some mechanisms
    (``drawdown``, ``correlation_breakdown``) ignore the per-world RNG seed
    entirely, so two scenario definitions that differ only in ``seed`` can
    produce byte-identical panels. A world identity that folded in the seed
    would wrongly report those as distinct evidence. The effective world
    identity must recognize them as the SAME evidence (see
    ``effective_world_hash``).
    """
    h = hashlib.sha256()
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


def effective_world_hash(
    base_digest: str,
    definition: ScenarioDefinition,
    panel: MarketDataPanel,
) -> str:
    """Canonical EFFECTIVE-WORLD identity for a generated scenario world.

    Deterministically ties the world to: the frozen baseline dataset digest it
    was perturbed from, the mechanism, the scenario parameters/intensity, the
    window, and -- most importantly -- the ACTUAL generated market content.

    The seed is intentionally EXCLUDED: two numerically identical market worlds
    (e.g. a ``drawdown`` world generated under two different seeds, or a
    confirmation world that happens to coincide with the primary) MUST collapse
    to the SAME effective identity. ``role`` (``primary`` vs ``confirmation``)
    is also excluded on purpose -- relabeling a world does not make it
    independent evidence.

    This is the identity used to prove Phase 5 confirmation disjointness:
    ``primary.world_hash not in confirmation_world_hashes`` and all
    ``confirmation_world_hashes`` pairwise distinct.
    """
    h = hashlib.sha256()
    h.update(base_digest.encode("utf-8"))
    h.update(b"|")
    h.update(definition.mechanism.encode("utf-8"))
    h.update(b"|")
    h.update(str(definition.intensity).encode("utf-8"))
    h.update(b"|")
    h.update(str(definition.start_index).encode("utf-8"))
    h.update(b"|")
    h.update(str(definition.duration).encode("utf-8"))
    h.update(b"|")
    h.update(definition.generator_version.encode("utf-8"))
    h.update(b"|")
    h.update(
        json.dumps(
            {k: str(v) for k, v in definition.parameters.items()},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    h.update(b"|")
    h.update(_panel_content_digest(panel).encode("utf-8"))
    return h.hexdigest()


def _panel_digest(panel: MarketDataPanel, definition: ScenarioDefinition) -> str:
    """Digest binds the FULL semantic definition (mechanism, seed, intensity,
    start_index, duration, parameters, generator_version) plus every panel
    dimension (Phase 2.6.1 gate: no semantic field excluded)."""
    h = hashlib.sha256()
    import json as _json

    h.update(_json.dumps(definition.to_dict(), sort_keys=True, separators=(",", ":")).encode())
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
    diagnostics: dict[str, Any] = {
        "mechanism": mech,
        "intensity": intensity,
        "start_index": start,
        "duration": duration,
    }

    if mech == ScenarioMechanism.DRAWDOWN:
        # Deterministic negative-return path over the declared interval.
        shock = -intensity
        per_bar = shock / duration
        factor = np.cumprod(np.full(duration, 1.0 + per_bar))
        close[start:end] = close[start:end] * factor[:, None]
        close[end:] = close[end:] * factor[-1]
        close = np.maximum(close, 1e-6)
        diagnostics["cumulative_shock"] = float(shock)
        diagnostics["per_bar_return_shock"] = float(per_bar)
        diagnostics["recovery_policy"] = "permanent level shift (no recovery)"

    elif mech == ScenarioMechanism.VOL_SPIKE:
        # Perturb returns INSIDE [start, end) only; bars before start are
        # untouched, bars after end differ only via level chaining.
        rets = _returns(base)  # shape (T-1, N); rets[t-1] is the return INTO bar t
        extra = np.zeros((T - 1, N))
        w0 = max(0, start - 1)
        w1 = max(w0, end - 1)
        if w1 > w0:
            extra[w0:w1] = rng.normal(0.0, intensity, size=(w1 - w0, N))
        new_close = np.empty((T, N))
        new_close[0] = base.close[0]
        growth = (1.0 + rets) * (1.0 + extra)
        new_close[1:] = base.close[0] * np.cumprod(growth, axis=0)
        new_close = np.maximum(new_close, 1e-6)
        # Bars strictly before `start` must equal the base exactly.
        new_close[:start] = base.close[:start]
        close = new_close
        pre_vol = float(np.std(rets[w0:w1])) if w1 > w0 else 0.0
        post_vol = float(np.std(rets[w0:w1] + extra[w0:w1])) if w1 > w0 else 0.0
        diagnostics["pre_volatility"] = pre_vol
        diagnostics["post_volatility"] = post_vol

    elif mech == ScenarioMechanism.CORRELATION_BREAKDOWN:
        # Rotate return pairs INSIDE the declared window only; symbol identity
        # preserved (a rotation of each pair's return vectors, not a column
        # permutation). Bars before `start` are untouched.
        rets = _returns(base)
        theta = intensity * np.pi  # rotation angle derived from intensity
        c, s = np.cos(theta), np.sin(theta)
        rot = np.array([[c, -s], [s, c]])
        out = rets.copy()
        w0 = max(0, start - 1)
        w1 = max(w0, end - 1)
        for k in range(0, N - (N % 2), 2):
            block = rets[w0:w1, k : k + 2] @ rot.T
            out[w0:w1, k : k + 2] = block
        new_close = np.empty((T, N))
        new_close[0] = base.close[0]
        new_close[1:] = base.close[0] * np.cumprod(1.0 + out, axis=0)
        new_close = np.maximum(new_close, 1e-6)
        new_close[:start] = base.close[:start]
        close = new_close
        if w1 > w0 and N >= 2:
            base_corr = np.corrcoef(rets[w0:w1].T)
            gen_corr = np.corrcoef(out[w0:w1].T)
            diagnostics["baseline_correlation_mean"] = float(np.mean(np.abs(base_corr - np.eye(N))))
            diagnostics["realized_correlation_mean"] = float(np.mean(np.abs(gen_corr - np.eye(N))))
        diagnostics["target_correlation"] = float(np.cos(theta))
        diagnostics["transformation_method"] = "deterministic_return_rotation_windowed"

    open_, high, low = _rebuild_ohlc(close)
    # Build a CANONICAL MarketDataPanel (Phase 3). The base may be a legacy
    # MarketDataPanel (from build_demo_panel) or an already-canonical panel;
    # read its fields defensively so both shapes work.
    scenario_id = str(uuid.uuid4())
    T, N = close.shape
    eligibility = np.ones((T, N), dtype=bool)
    observed = np.ones((T, N), dtype=bool)
    imputed = np.zeros((T, N), dtype=bool)
    legacy_assets = getattr(base, "assets", None)
    base_instruments = getattr(base, "instruments", None)
    if base_instruments is not None:
        instruments = tuple(base_instruments)
    elif legacy_assets is not None:
        instruments = tuple(
            InstrumentIdentifier(symbol=a, asset_type=AssetType.EQUITY, currency="USD") for a in legacy_assets
        )
    else:
        instruments = tuple(
            InstrumentIdentifier(symbol=f"A{i}", asset_type=AssetType.EQUITY, currency="USD")
            for i in range(N)
        )
    benchmark_instrument = getattr(base, "benchmark_instrument", None)
    panel = MarketDataPanel(
        dates=base.dates,
        instruments=instruments,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=base.volume,
        benchmark_close=getattr(base, "benchmark_close", None),
        benchmark_instrument=benchmark_instrument,
        eligibility_mask=eligibility,
        observed_mask=observed,
        imputation_mask=imputed,
        provider=getattr(base, "provider", "synthetic_fixture"),
        provider_version=getattr(base, "provider_version", "unknown"),
        retrieval_timestamp=getattr(base, "retrieval_timestamp", datetime.now(UTC)),
        as_of=getattr(base, "as_of", None),
        calendar_policy=getattr(base, "calendar_policy", CalendarPolicy.SYNTHETIC_WEEKDAY),
        adjustment_policy=getattr(base, "adjustment_policy", AdjustmentPolicy.RAW),
        missing_data_policy=getattr(base, "missing_data_policy", "none"),
        eligibility_source=getattr(base, "eligibility_source", EligibilitySource.SYNTHETIC_FIXTURE),
        source_metadata={
            **getattr(base, "source_metadata", {}),
            "scenario": mech,
            "scenario_id": scenario_id,
        },
    )
    return GeneratedScenario(
        scenario_id=scenario_id,
        definition=definition,
        panel=panel,
        content_digest=_panel_digest(panel, definition),
        diagnostics=diagnostics,
    )


def assert_panel_invariants(panel: MarketDataPanel) -> None:
    """Raise ValueError unless every bar/asset satisfies the OHLCV invariants:

    * finite OHLC, positive open/close
    * low <= min(open, close)
    * high >= max(open, close)
    * volume finite and non-negative
    """
    open_ = panel.open
    high = panel.high
    low = panel.low
    close = panel.close
    volume = panel.volume
    if not (
        np.all(np.isfinite(open_))
        and np.all(np.isfinite(high))
        and np.all(np.isfinite(low))
        and np.all(np.isfinite(close))
    ):
        raise ValueError("non-finite OHLC value in panel")
    if np.any(open_ <= 0) or np.any(close <= 0):
        raise ValueError("non-positive open/close in panel")
    lo_bound = np.minimum(open_, close)
    hi_bound = np.maximum(open_, close)
    if np.any(low > lo_bound + 1e-12):
        t, j = np.unravel_index(int(np.argmax(low - lo_bound)), low.shape)
        raise ValueError(f"low > min(open, close) at ({t},{j})")
    if np.any(high < hi_bound - 1e-12):
        t, j = np.unravel_index(int(np.argmax(hi_bound - high)), high.shape)
        raise ValueError(f"high < max(open, close) at ({t},{j})")
    if np.any(high < low):
        raise ValueError("high < low in panel")
    if volume is not None:
        if not np.all(np.isfinite(volume)):
            raise ValueError("non-finite volume in panel")
        if np.any(volume < 0):
            raise ValueError("negative volume in panel")


__all__ = [
    "ScenarioMechanism",
    "ScenarioDefinition",
    "GeneratedScenario",
    "KNOWN_MECHANISMS",
    "validate_mechanisms",
    "generate_scenario",
    "assert_panel_invariants",
    "stable_seed",
    "effective_world_hash",
    "_panel_content_digest",
]
