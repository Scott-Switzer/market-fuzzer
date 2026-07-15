from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field


class ParticipationClaimGate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = "execution_cost_increases_with_participation"
    mean_shortfall_bps: float
    median_shortfall_bps: float
    bootstrap_mean_interval_bps: tuple[float, float]
    positive_paired_change_fraction: float = Field(ge=0.0, le=1.0)
    spearman_rho: float
    bootstrap_slope_interval: tuple[float, float]
    calibration_set_agreement: float = Field(ge=0.0, le=1.0)
    thresholds: dict[str, float]
    permitted: bool
    blocking_reasons: list[str]
    uncertainty_diagnosis: list[str]
    observations: int


def _ranks(values: list[float]) -> list[float]:
    ordered = sorted((value, index) for index, value in enumerate(values))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and ordered[end][0] == ordered[cursor][0]:
            end += 1
        rank = (cursor + end - 1) / 2 + 1
        for _, index in ordered[cursor:end]:
            ranks[index] = rank
        cursor = end
    return ranks


def pearson(left: Iterable[float], right: Iterable[float]) -> float:
    x, y = list(left), list(right)
    if len(x) != len(y) or len(x) < 2:
        return 0.0
    mx, my = statistics.fmean(x), statistics.fmean(y)
    dx, dy = [v - mx for v in x], [v - my for v in y]
    denominator = math.sqrt(sum(v * v for v in dx) * sum(v * v for v in dy))
    return sum(a * b for a, b in zip(dx, dy, strict=True)) / denominator if denominator else 0.0


def spearman(left: Iterable[float], right: Iterable[float]) -> float:
    x, y = list(left), list(right)
    return pearson(_ranks(x), _ranks(y))


def _slope(points: list[tuple[float, float]]) -> float:
    x = [point[0] for point in points]
    y = [point[1] for point in points]
    mean_x, mean_y = statistics.fmean(x), statistics.fmean(y)
    denominator = sum((value - mean_x) ** 2 for value in x)
    return sum((a - mean_x) * (b - mean_y) for a, b in points) / denominator if denominator else 0.0


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    return (
        ordered[lower]
        if lower == upper
        else ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    )


def evaluate_participation_claim(rows: list[dict], bootstrap_seed: int = 991) -> ParticipationClaimGate:
    if not rows:
        raise ValueError("participation claim requires run rows")
    values = [float(row["implementation_shortfall_bps"]) for row in rows]
    grouped: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["calibration_parameter_set_id"]), int(row["seed"]))].append(
            (float(row["participation_rate"]), float(row["implementation_shortfall_bps"]))
        )
    pairs = [sorted(points) for points in grouped.values() if len(points) >= 2]
    paired_changes = [point[1] - points[0][1] for points in pairs for point in points[1:]]
    positive_fraction = (
        sum(change > 0 for change in paired_changes) / len(paired_changes) if paired_changes else 0.0
    )

    by_rate: dict[float, list[float]] = defaultdict(list)
    for row in rows:
        by_rate[float(row["participation_rate"])].append(float(row["implementation_shortfall_bps"]))
    rate_means = sorted((rate, statistics.fmean(costs)) for rate, costs in by_rate.items())
    rho = spearman([point[0] for point in rate_means], [point[1] for point in rate_means])

    rng = random.Random(bootstrap_seed)
    mean_draws: list[float] = []
    slope_draws: list[float] = []
    for _ in range(600):
        sampled = [rng.choice(pairs) for _ in pairs] if pairs else []
        sampled_values = [cost for points in sampled for _, cost in points]
        mean_draws.append(statistics.fmean(sampled_values) if sampled_values else statistics.fmean(values))
        boot_rates: dict[float, list[float]] = defaultdict(list)
        for points in sampled:
            for rate, cost in points:
                boot_rates[rate].append(cost)
        boot_points = sorted((rate, statistics.fmean(costs)) for rate, costs in boot_rates.items())
        slope_draws.append(_slope(boot_points) if len(boot_points) > 1 else 0.0)

    by_set: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_set[str(row["calibration_parameter_set_id"])].append(row)
    set_passes = 0
    for set_rows in by_set.values():
        set_rates: dict[float, list[float]] = defaultdict(list)
        for row in set_rows:
            set_rates[float(row["participation_rate"])].append(float(row["implementation_shortfall_bps"]))
        points = sorted((rate, statistics.fmean(costs)) for rate, costs in set_rates.items())
        set_rho = spearman([point[0] for point in points], [point[1] for point in points])
        set_pairs = [
            points
            for key, points in grouped.items()
            if key[0] == str(set_rows[0]["calibration_parameter_set_id"])
        ]
        changes = [point[1] - series[0][1] for series in set_pairs for point in series[1:]]
        set_positive = sum(change > 0 for change in changes) / len(changes) if changes else 0.0
        set_passes += int(set_rho >= 0.70 and set_positive >= 0.70)
    agreement = set_passes / len(by_set) if by_set else 0.0
    slope_interval = (_quantile(slope_draws, 0.025), _quantile(slope_draws, 0.975))
    mean_interval = (_quantile(mean_draws, 0.025), _quantile(mean_draws, 0.975))
    reasons = []
    diagnosis = []
    if rho < 0.70:
        reasons.append("Spearman trend is below 0.70")
        diagnosis.append("poor interventional fidelity or non-monotone response")
    if positive_fraction < 0.70:
        reasons.append("positive paired-change fraction is below 0.70")
        diagnosis.append("seed variance dominates the paired response")
    if slope_interval[0] <= 0:
        reasons.append("bootstrap slope lower bound is not above zero")
        diagnosis.append("aggregate slope remains statistically uncertain")
    if agreement < 0.80:
        reasons.append("accepted-calibration agreement is below 0.80")
        diagnosis.append("calibration uncertainty changes the conclusion")
    return ParticipationClaimGate(
        mean_shortfall_bps=statistics.fmean(values),
        median_shortfall_bps=statistics.median(values),
        bootstrap_mean_interval_bps=mean_interval,
        positive_paired_change_fraction=positive_fraction,
        spearman_rho=rho,
        bootstrap_slope_interval=slope_interval,
        calibration_set_agreement=agreement,
        thresholds={
            "spearman_rho_min": 0.70,
            "positive_pair_fraction_min": 0.70,
            "bootstrap_slope_lower_bound_gt": 0.0,
            "calibration_ensemble_pass_fraction_min": 0.80,
        },
        permitted=not reasons,
        blocking_reasons=reasons,
        uncertainty_diagnosis=sorted(set(diagnosis)),
        observations=len(rows),
    )
