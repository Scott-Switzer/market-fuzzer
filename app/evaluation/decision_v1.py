"""Deterministic uncertainty evidence for strategy comparisons.

Primary comparisons are paired by world/seed so common random numbers reduce
noise.  This module reports uncertainty and evidence insufficiency; it never
selects worlds or changes a primary score.
"""

from __future__ import annotations

import json
import math
import random
import statistics
from dataclasses import dataclass
from hashlib import sha256

from .sealed_v1 import PrimaryEvaluationResultV1


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


@dataclass(frozen=True, slots=True)
class AdjustedDecisionEvidenceV1:
    metric_name: str
    raw_p_value: float
    adjusted_p_value: float
    discovery_supported: bool


def _policy_digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class DecisionMetricPolicyV1:
    """Precommitted metric vector and ranking weights for a sealed campaign."""

    metric_names: tuple[str, ...]
    ranking_weights: tuple[tuple[str, float], ...]
    false_discovery_rate: float = 0.05
    policy_version: str = "decision-metric-policy-v1"

    def __post_init__(self) -> None:
        if not self.metric_names or len(set(self.metric_names)) != len(self.metric_names):
            raise ValueError("decision metric policy requires unique metric names")
        if any(not name for name in self.metric_names):
            raise ValueError("decision metric names must be non-empty")
        weights = dict(self.ranking_weights)
        if len(weights) != len(self.ranking_weights) or set(weights) != set(self.metric_names):
            raise ValueError("decision metric policy weights must cover each metric exactly once")
        if any(not math.isfinite(weight) for weight in weights.values()):
            raise ValueError("decision metric policy weights must be finite")
        if not any(weight != 0 for weight in weights.values()):
            raise ValueError("decision metric policy requires at least one non-zero ranking weight")
        if not 0 < self.false_discovery_rate < 1:
            raise ValueError("false discovery rate must be between zero and one")

    @property
    def digest(self) -> str:
        return _policy_digest(
            {
                "policy_version": self.policy_version,
                "metric_names": list(self.metric_names),
                "ranking_weights": sorted(self.ranking_weights),
                "false_discovery_rate": self.false_discovery_rate,
            }
        )


@dataclass(frozen=True, slots=True)
class SealedDecisionReportV1:
    """Metric-vector evidence, intentionally separate from a scalar leaderboard rank."""

    campaign_commitment_digest: str
    scoring_policy_digest: str
    candidate_artifact_digest: str
    baseline_artifact_digest: str
    evidence: tuple[DecisionEvidenceV1, ...]
    multiplicity: tuple[AdjustedDecisionEvidenceV1, ...]
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


def sealed_metric_decision_evidence(
    metric_name: str,
    candidate: PrimaryEvaluationResultV1,
    baseline: PrimaryEvaluationResultV1,
    *,
    bootstrap_draws: int = 2_000,
    bootstrap_seed: int = 991,
) -> DecisionEvidenceV1:
    """Compare two frozen artifacts only on the same sealed campaign receipts."""
    if candidate.campaign_commitment_digest != baseline.campaign_commitment_digest:
        raise ValueError("sealed metric comparison requires the same campaign commitment")
    if candidate.strategy_artifact_digest == baseline.strategy_artifact_digest:
        raise ValueError("sealed metric comparison requires distinct frozen artifacts")
    candidate_values = {
        metric.world_receipt: metric.value
        for metric in candidate.metrics
        if metric.metric_name == metric_name
    }
    baseline_values = {
        metric.world_receipt: metric.value for metric in baseline.metrics if metric.metric_name == metric_name
    }
    receipts = {world.world_receipt for world in candidate.worlds}
    if (
        receipts != {world.world_receipt for world in baseline.worlds}
        or set(candidate_values) != receipts
        or set(baseline_values) != receipts
    ):
        raise ValueError("sealed metric coverage must match every opaque primary receipt")
    return paired_decision_evidence(
        metric_name,
        [
            PairedOutcomeV1(receipt, "sealed_primary", candidate_values[receipt], baseline_values[receipt])
            for receipt in sorted(receipts)
        ],
        bootstrap_draws=bootstrap_draws,
        bootstrap_seed=bootstrap_seed,
    )


def benjamini_hochberg_adjust(
    evidence: list[DecisionEvidenceV1], *, false_discovery_rate: float = 0.05
) -> tuple[AdjustedDecisionEvidenceV1, ...]:
    """Apply declared BH adjustment; metrics without a p-value cannot be discoveries."""
    if not 0 < false_discovery_rate < 1:
        raise ValueError("false_discovery_rate must be between zero and one")
    eligible = sorted(
        (item for item in evidence if item.two_sided_sign_p_value is not None),
        key=lambda item: (
            item.two_sided_sign_p_value if item.two_sided_sign_p_value is not None else 1.0,
            item.metric_name,
        ),
    )
    total = len(eligible)
    adjusted: dict[str, float] = {}
    running = 1.0
    for rank, item in reversed(list(enumerate(eligible, start=1))):
        assert item.two_sided_sign_p_value is not None
        running = min(running, item.two_sided_sign_p_value * total / rank)
        adjusted[item.metric_name] = running
    return tuple(
        AdjustedDecisionEvidenceV1(
            item.metric_name,
            item.two_sided_sign_p_value if item.two_sided_sign_p_value is not None else 1.0,
            adjusted.get(item.metric_name, 1.0),
            item.verdict == "evidence_of_difference"
            and adjusted.get(item.metric_name, 1.0) <= false_discovery_rate,
        )
        for item in sorted(evidence, key=lambda item: item.metric_name)
    )


def sealed_decision_report(
    policy: DecisionMetricPolicyV1,
    candidate: PrimaryEvaluationResultV1,
    baseline: PrimaryEvaluationResultV1,
) -> SealedDecisionReportV1:
    """Build a policy-bound metric report without deriving a post-hoc primary score."""
    if candidate.scoring_policy_digest != policy.digest or baseline.scoring_policy_digest != policy.digest:
        raise ValueError("sealed decision report requires the campaign's committed metric policy")
    evidence = tuple(
        sealed_metric_decision_evidence(
            metric_name,
            candidate,
            baseline,
            bootstrap_seed=int.from_bytes(
                sha256(f"{policy.digest}:{metric_name}".encode()).digest()[:8], "big"
            ),
        )
        for metric_name in policy.metric_names
    )
    return SealedDecisionReportV1(
        candidate.campaign_commitment_digest,
        policy.digest,
        candidate.strategy_artifact_digest,
        baseline.strategy_artifact_digest,
        evidence,
        benjamini_hochberg_adjust(list(evidence), false_discovery_rate=policy.false_discovery_rate),
        (
            "Ranking weights were precommitted but are not used to turn this evidence vector into a scalar claim.",
            "Supported differences apply only to this sealed synthetic campaign, not live profitability.",
        ),
    )
