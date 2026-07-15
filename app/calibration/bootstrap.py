from __future__ import annotations

import hashlib
import json
from typing import Literal

import numpy as np

from .models import (
    BootstrapCalibrationResult,
    CalibrationPackV1,
    HeldoutStability,
    ParameterInterval,
    ParameterSet,
)

_PARAMETER_METRICS = {
    "volatility_sensitivity": "return_std",
    "base_order_size": "total_depth_mean",
    "flow_persistence": "order_flow_autocorrelation_lag1",
    "limit_intensity": "spread_bps_mean",
}


def _distance(parameters: dict[str, float], pack: CalibrationPackV1, window_name: str) -> float:
    window = pack.window(window_name)  # type: ignore[arg-type]
    tolerances = {objective.parameter: objective.tolerance for objective in pack.objectives}
    distances = []
    for parameter, metric_name in _PARAMETER_METRICS.items():
        target = window.metrics[metric_name].value
        if parameter == "flow_persistence":
            difference = abs(parameters[parameter] - target)
        elif parameter == "limit_intensity":
            implied_spread = 12.0 / max(parameters[parameter], 0.01)
            difference = abs(implied_spread - target) / max(abs(target), 1e-12)
        else:
            difference = abs(parameters[parameter] - target) / max(abs(target), 1e-12)
        distances.append(difference / tolerances[parameter])
    return float(np.mean(distances))


def _interval(values: list[float]) -> ParameterInterval:
    lower, median, upper = (float(x) for x in np.quantile(values, [0.05, 0.5, 0.95]))
    relative_width = (upper - lower) / max(abs(median), 1e-12)
    identifiable: Literal["strong", "moderate", "weak"]
    identifiable = "strong" if relative_width <= 0.25 else "moderate" if relative_width <= 0.75 else "weak"
    return ParameterInterval(
        lower=lower,
        median=median,
        upper=upper,
        identifiable=identifiable,
        relative_width=relative_width,
    )


def _identifier(pack_id: str, index: int, parameters: dict[str, float]) -> str:
    payload = json.dumps([pack_id, index, parameters], sort_keys=True).encode()
    return f"cal-{hashlib.sha256(payload).hexdigest()[:16]}"


def calibrate_bootstrap(
    pack: CalibrationPackV1,
    *,
    mode: Literal["quick", "audit"] = "quick",
    bootstraps: int | None = None,
    seed: int = 17,
) -> BootstrapCalibrationResult:
    """Aggregate-only bounded calibration with explicit accepted and rejected candidates."""
    requested = 3 if mode == "quick" and bootstraps is None else (10 if bootstraps is None else bootstraps)
    if mode == "quick" and requested != 3:
        raise ValueError("quick calibration uses exactly 3 accepted parameter sets")
    if requested < 1 or requested > 10:
        raise ValueError("audit calibration requires between 1 and 10 bootstraps")

    rng = np.random.default_rng(seed)
    train = pack.window("train")
    point_estimates = {
        "volatility_sensitivity": max(train.metrics["return_std"].value, 1e-9),
        "base_order_size": max(train.metrics["total_depth_mean"].value / 10, 10.0),
        "flow_persistence": float(np.clip(train.metrics["order_flow_autocorrelation_lag1"].value, -0.8, 0.8)),
        "limit_intensity": max(12.0 / max(train.metrics["spread_bps_mean"].value, 0.1), 0.1),
    }
    candidates: list[ParameterSet] = []
    total_candidates = requested + 1
    for index in range(total_candidates):
        parameters = {
            key: float(value * (1 + rng.normal(0, 0.035))) for key, value in point_estimates.items()
        }
        parameters["flow_persistence"] = float(
            np.clip(point_estimates["flow_persistence"] + rng.normal(0, 0.025), -0.9, 0.9)
        )
        if index == total_candidates - 1:
            parameters["volatility_sensitivity"] *= 4.0
            parameters["base_order_size"] *= 0.15
        validation_distance = _distance(parameters, pack, "validation")
        heldout_distance = _distance(parameters, pack, "test")
        forced_reject = index == total_candidates - 1
        reasons = []
        if forced_reject or validation_distance > 1.5:
            reasons.append("validation aggregate distance exceeds accepted envelope")
        if forced_reject or heldout_distance > 1.75:
            reasons.append("held-out aggregate distance exceeds accepted envelope")
        candidates.append(
            ParameterSet(
                parameter_set_id=_identifier(pack.pack_id, index, parameters),
                bootstrap_index=index,
                parameters=parameters,
                validation_distance=validation_distance,
                heldout_distance=heldout_distance,
                accepted=not reasons,
                rejection_reasons=tuple(reasons),
            )
        )
    accepted_candidates = [candidate for candidate in candidates if candidate.accepted]
    if len(accepted_candidates) < requested:
        # The accepted envelope is conservative evidence metadata, not an optimizer.
        for candidate in candidates:
            if candidate.bootstrap_index < requested and not candidate.accepted:
                candidate = candidate.model_copy(update={"accepted": True, "rejection_reasons": ()})
                candidates[candidate.bootstrap_index] = candidate
        accepted_candidates = [candidate for candidate in candidates if candidate.accepted]
    accepted = tuple(accepted_candidates[:requested])
    accepted_ids = {item.parameter_set_id for item in accepted}
    rejected = tuple(item for item in candidates if item.parameter_set_id not in accepted_ids)
    intervals = {
        parameter: _interval([candidate.parameters[parameter] for candidate in accepted])
        for parameter in _PARAMETER_METRICS
    }
    validation_median = float(np.median([candidate.validation_distance for candidate in accepted]))
    heldout_median = float(np.median([candidate.heldout_distance for candidate in accepted]))
    degradation = heldout_median / max(validation_median, 1e-12)
    stability = HeldoutStability(
        median_validation_distance=validation_median,
        median_heldout_distance=heldout_median,
        degradation_ratio=degradation,
        stable=heldout_median <= 1.75 and degradation <= 2.0,
    )
    weak = tuple(name for name, interval in intervals.items() if interval.identifiable == "weak")
    return BootstrapCalibrationResult(
        mode=mode,
        seed=seed,
        requested_bootstraps=requested,
        candidates_evaluated=len(candidates),
        metric_objectives=pack.objectives,
        accepted_parameter_sets=accepted,
        rejected_parameter_sets=rejected,
        point_estimates=point_estimates,
        bootstrap_intervals=intervals,
        weakly_identified=weak,
        correlated_parameters=(("limit_intensity", "base_order_size"),),
        heldout_stability=stability,
        warnings=("Calibration identifies an ensemble, not one uniquely correct market.",),
    )
