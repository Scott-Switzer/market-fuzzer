"""Transient generated-versus-reference trajectory similarity diagnostics.

These checks are evidence against copied historical paths, not a proof of novelty.
Reference prices are accepted only in memory and never appear in the report.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.generators.v1 import GeneratedWorldV1


class TrajectorySimilarityReportV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1.0"
    generated_world_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    generated_return_count: int = Field(ge=2)
    reference_return_count: int = Field(ge=2)
    nearest_window_correlation: float = Field(ge=-1.0, le=1.0)
    nearest_window_normalized_rmse: float = Field(ge=0.0)
    exact_return_window_duplicate: bool
    similarity_warning: bool
    limitation: str = (
        "Similarity checks provide evidence against copied trajectories; they do not prove novelty."
    )


def _log_returns(prices: Sequence[float]) -> np.ndarray:
    values = np.asarray(prices, dtype=float)
    if len(values) < 3 or not np.all(np.isfinite(values)) or np.any(values <= 0):
        raise ValueError("price trajectory requires at least three finite positive observations")
    return np.diff(np.log(values))


def _normalized(values: np.ndarray) -> np.ndarray:
    scale = float(np.std(values))
    return (values - float(np.mean(values))) / (scale if scale > 1e-15 else 1.0)


def generated_world_similarity(
    world: GeneratedWorldV1,
    reference_prices: Sequence[float],
    *,
    instrument_id: str | None = None,
    exact_tolerance: float = 1e-12,
    correlation_warning_threshold: float = 0.995,
) -> TrajectorySimilarityReportV1:
    """Compare a generated price path to all same-length historical windows in memory."""
    if exact_tolerance < 0 or not -1.0 <= correlation_warning_threshold <= 1.0:
        raise ValueError("similarity thresholds are out of bounds")
    instruments = {event.instrument_id for event in world.events}
    if instrument_id is None:
        if len(instruments) != 1:
            raise ValueError("multi-instrument world similarity requires an instrument_id")
        instrument_id = next(iter(instruments))
    prices = [event.price_ticks for event in world.events if event.instrument_id == instrument_id]
    if not prices:
        raise ValueError("requested instrument has no generated trajectory")
    generated = _log_returns(prices)
    reference = _log_returns(reference_prices)
    if len(reference) < len(generated):
        raise ValueError("reference path must have at least as many returns as generated world")
    normalized_generated = _normalized(generated)
    correlations: list[float] = []
    rmses: list[float] = []
    exact_duplicate = False
    for start in range(len(reference) - len(generated) + 1):
        window = reference[start : start + len(generated)]
        normalized_window = _normalized(window)
        if float(np.std(normalized_generated)) == 0.0 or float(np.std(normalized_window)) == 0.0:
            correlation = 1.0 if np.allclose(normalized_generated, normalized_window) else 0.0
        else:
            correlation = float(np.corrcoef(normalized_generated, normalized_window)[0, 1])
        correlations.append(max(-1.0, min(1.0, correlation)))
        rmses.append(float(np.sqrt(np.mean((normalized_generated - normalized_window) ** 2))))
        exact_duplicate = exact_duplicate or bool(
            np.allclose(generated, window, rtol=0.0, atol=exact_tolerance)
        )
    nearest_index = max(range(len(correlations)), key=lambda index: correlations[index])
    nearest_correlation = correlations[nearest_index]
    nearest_rmse = rmses[nearest_index]
    return TrajectorySimilarityReportV1(
        generated_world_digest=world.digest,
        reference_checksum="sha256:"
        + hashlib.sha256(
            json.dumps([float(price) for price in reference_prices], separators=(",", ":")).encode()
        ).hexdigest(),
        generated_return_count=len(generated),
        reference_return_count=len(reference),
        nearest_window_correlation=nearest_correlation,
        nearest_window_normalized_rmse=nearest_rmse,
        exact_return_window_duplicate=exact_duplicate,
        similarity_warning=exact_duplicate or nearest_correlation >= correlation_warning_threshold,
    )
