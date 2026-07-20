"""Deterministic uncertainty evidence for strategy comparisons.

Primary comparisons are paired by world/seed so common random numbers reduce
noise.  This module reports uncertainty and evidence insufficiency; it never
selects worlds or changes a primary score.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PairedOutcomeV1:
    block_id: str
    generator_family: str
    candidate_value: float
    baseline_value: float

    @property
    def delta(self) -> float:
        return self.candidate_value - self.baseline_value


@dataclass(frozen=True, slots=True)
class DecisionEvidenceV1:
    metric_name: str
    sample_size: int
    effect_size: float | None
    confidence_interval: tuple[float, float] | None
    two_sided_sign_p_value: float | None
    family_effects: tuple[tuple[str, float], ...]
    verdict: str
    limitations: tuple[str, ...]


def _quantile(sorted_values: list[float], probability: float) -> float:
    index = min(len(sorted_values) - 1, max(0, round((len(sorted_values) - 1) * probability)))
    return sorted_values[index]


def _sign_p_value(deltas: list[float]) -> float:
    nonzero = [value for value in deltas if value != 0]
    if not nonzero:
        return 1.0
    positive = sum(value > 0 for value in nonzero)
    # Exact two-sided binomial tail, without a normal approximation.
    from math import comb

    tail = sum(comb(len(nonzero), count) for count in range(positive + 1)) / 2 ** len(nonzero)
    tail = min(tail, 1 - tail + comb(len(nonzero), positive) / 2 ** len(nonzero))
    return min(1.0, 2 * tail)


def paired_decision_evidence(
    metric_name: str,
    outcomes: list[PairedOutcomeV1],
    *,
    bootstrap_draws: int = 2_000,
    bootstrap_seed: int = 991,
    minimum_pairs: int = 8,
) -> DecisionEvidenceV1:
    """Block-bootstrap paired outcomes; reports insufficient evidence honestly."""
    if bootstrap_draws < 100:
        raise ValueError("bootstrap_draws must be at least 100")
    if len({outcome.block_id for outcome in outcomes}) != len(outcomes):
        raise ValueError("paired outcomes must have unique block IDs")
    deltas = [outcome.delta for outcome in outcomes]
    family_effects = tuple(
        (
            family,
            statistics.fmean(outcome.delta for outcome in outcomes if outcome.generator_family == family),
        )
        for family in sorted({outcome.generator_family for outcome in outcomes})
    )
    if len(outcomes) < minimum_pairs:
        return DecisionEvidenceV1(
            metric_name,
            len(outcomes),
            None,
            None,
            None,
            family_effects,
            "insufficient_evidence",
            (f"Requires at least {minimum_pairs} paired blocks; received {len(outcomes)}.",),
        )
    rng = random.Random(bootstrap_seed)
    draws = sorted(
        statistics.fmean(deltas[rng.randrange(len(deltas))] for _ in deltas) for _ in range(bootstrap_draws)
    )
    interval = (_quantile(draws, 0.025), _quantile(draws, 0.975))
    verdict = "evidence_of_difference" if interval[0] > 0 or interval[1] < 0 else "insufficient_evidence"
    return DecisionEvidenceV1(
        metric_name,
        len(outcomes),
        statistics.fmean(deltas),
        interval,
        _sign_p_value(deltas),
        family_effects,
        verdict,
        (
            "Paired blocks use common random numbers; finite generator coverage does not establish live-market performance.",
        ),
    )
