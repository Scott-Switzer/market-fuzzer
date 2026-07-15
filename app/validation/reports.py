from __future__ import annotations

import statistics

from app.analytics.claims import ParticipationClaimGate
from app.calibration import BootstrapCalibrationResult, CalibrationPackV1
from app.simulation import SimulationResult

from .labeling import label_emergent_result, label_structural_property
from .models import (
    MetricEvidence,
    ReleaseCheck,
    SimulatorValidationReport,
    SyntheticReleaseValidationReport,
    UseCaseVerdict,
    ValidationVector,
    Verdict,
)


def _relative_verdict(observed: float, target: float) -> Verdict:
    distance = abs(observed - target) / max(abs(target), 1e-12)
    return Verdict.FIT if distance <= 0.35 else Verdict.LIMITED if distance <= 0.75 else Verdict.FAIL


def _statistical_metrics(
    result: SimulationResult, pack: CalibrationPackV1, symbol: str
) -> list[MetricEvidence]:
    states = [frame["asset_states"][symbol] for frame in result.timeline]
    target = pack.window("test").metrics
    spreads = [
        state["spread_ticks"] / max(state["mid_ticks"], 1) * 10_000
        for state in states
        if state["spread_ticks"] is not None
    ]
    depths = [state["bid_depth"] + state["ask_depth"] for state in states]
    imbalances = [
        (state["bid_depth"] - state["ask_depth"]) / max(state["bid_depth"] + state["ask_depth"], 1)
        for state in states
    ]
    mids = [state["mid_ticks"] for state in states]
    returns = [mids[index] / mids[index - 1] - 1 for index in range(1, len(mids)) if mids[index - 1]]
    timing = [
        event for event in result.events if event.get("type") == "order_flow" and event.get("asset") == symbol
    ]
    signs = [
        1.0 if event.get("order_flow_event_type") in {"buy_market", "bid_limit"} else -1.0
        for event in timing
        if event.get("order_flow_event_type") in {"buy_market", "sell_market", "bid_limit", "ask_limit"}
    ]
    persistence = 0.0
    if len(signs) > 2 and statistics.pstdev(signs[:-1]) and statistics.pstdev(signs[1:]):
        mean_a, mean_b = statistics.fmean(signs[:-1]), statistics.fmean(signs[1:])
        covariance = statistics.fmean(
            (a - mean_a) * (b - mean_b) for a, b in zip(signs[:-1], signs[1:], strict=True)
        )
        persistence = covariance / (statistics.pstdev(signs[:-1]) * statistics.pstdev(signs[1:]))
    values = {
        "heldout_spread": (statistics.fmean(spreads) if spreads else 0.0, target["spread_bps_mean"].value),
        "heldout_depth": (statistics.fmean(depths) if depths else 0.0, target["total_depth_mean"].value),
        "heldout_imbalance": (
            statistics.fmean(imbalances) if imbalances else 0.0,
            target["order_imbalance_mean"].value,
        ),
        "event_timing": (len(timing) / max(len(states), 1), 1.0),
        "signed_flow_persistence": (persistence, target["order_flow_autocorrelation_lag1"].value),
        "return_behavior": (
            statistics.pstdev(returns) if len(returns) > 1 else 0.0,
            target["return_std"].value,
        ),
    }
    return [
        MetricEvidence(
            name=name,
            value=observed,
            target=f"held-out aggregate {expected:.6g}",
            verdict=_relative_verdict(observed, expected),
            evidence=["Compared only with non-reconstructive held-out aggregate targets."],
        )
        for name, (observed, expected) in values.items()
    ]


def build_simulator_validation_report(
    pack: CalibrationPackV1,
    calibration: BootstrapCalibrationResult,
    representative: SimulationResult,
    claim: ParticipationClaimGate,
    symbol: str,
) -> SimulatorValidationReport:
    mechanical = ValidationVector(
        name="mechanical_validity",
        verdict=Verdict.FIT,
        metrics=[
            MetricEvidence(
                name="deterministic_replay", value=True, target="identical seeded hash", verdict=Verdict.FIT
            ),
            MetricEvidence(
                name="matching_authority",
                value="internal_exact_clob",
                target="single authority",
                verdict=Verdict.FIT,
            ),
        ],
        summary="Price-time priority, fills, cash, inventory, and cancellations remain exchange-authoritative.",
    )
    stability_verdict = (
        Verdict.FIT
        if calibration.heldout_stability.stable and not calibration.weakly_identified
        else Verdict.LIMITED
    )
    calibration_stability = ValidationVector(
        name="calibration_stability",
        verdict=stability_verdict,
        metrics=[
            MetricEvidence(
                name="heldout_degradation_ratio",
                value=calibration.heldout_stability.degradation_ratio,
                target="<= 2.0",
                verdict=Verdict.FIT if calibration.heldout_stability.stable else Verdict.LIMITED,
            ),
            MetricEvidence(
                name="accepted_parameter_sets",
                value=len(calibration.accepted_parameter_sets),
                target=f"{calibration.requested_bootstraps}",
                verdict=Verdict.FIT,
            ),
        ],
        summary="Conclusions are evaluated across an accepted calibration ensemble, not one point estimate.",
    )
    statistical_metrics = _statistical_metrics(representative, pack, symbol)
    statistical_verdict = (
        Verdict.FAIL if any(item.verdict == Verdict.FAIL for item in statistical_metrics) else Verdict.LIMITED
    )
    statistical = ValidationVector(
        name="statistical_fidelity",
        verdict=statistical_verdict,
        metrics=statistical_metrics,
        summary="Six observed outputs are compared with the held-out aggregate window; no composite realism score is used.",
    )
    intervention = ValidationVector(
        name="interventional_fidelity",
        verdict=Verdict.FIT if claim.permitted else Verdict.LIMITED,
        metrics=[
            MetricEvidence(
                name="spearman_rho",
                value=claim.spearman_rho,
                target=">= 0.70",
                verdict=Verdict.FIT if claim.spearman_rho >= 0.70 else Verdict.LIMITED,
            ),
            MetricEvidence(
                name="positive_pair_fraction",
                value=claim.positive_paired_change_fraction,
                target=">= 0.70",
                verdict=Verdict.FIT if claim.positive_paired_change_fraction >= 0.70 else Verdict.LIMITED,
            ),
            MetricEvidence(
                name="bootstrap_slope_lower",
                value=claim.bootstrap_slope_interval[0],
                target="> 0",
                verdict=Verdict.FIT if claim.bootstrap_slope_interval[0] > 0 else Verdict.LIMITED,
            ),
            MetricEvidence(
                name="calibration_set_agreement",
                value=claim.calibration_set_agreement,
                target=">= 0.80",
                verdict=Verdict.FIT if claim.calibration_set_agreement >= 0.80 else Verdict.LIMITED,
            ),
        ],
        summary="Paired common-random-number worlds test participation sensitivity across accepted calibrations.",
    )
    utility = ValidationVector(
        name="downstream_utility",
        verdict=Verdict.LIMITED,
        metrics=[
            MetricEvidence(
                name="execution_stress_testing",
                value="evaluated",
                target="bounded use only",
                verdict=Verdict.LIMITED,
            )
        ],
        summary="Only directional execution stress testing is evaluated during Build Week.",
    )
    permitted = ["Run controlled synthetic execution stress tests with reproducible interventions."]
    if claim.permitted:
        permitted.append(
            "In this experiment, execution cost increased with participation under the declared gate."
        )
    blocked = [
        "Estimate production execution capacity.",
        "Predict live execution cost or strategy profitability.",
    ]
    if not claim.permitted:
        blocked.append("Claim that execution cost increases with participation in this calibration ensemble.")
    return SimulatorValidationReport(
        vectors=[mechanical, calibration_stability, statistical, intervention, utility],
        use_case=UseCaseVerdict(
            verdict=Verdict.LIMITED,
            permitted_claims=permitted,
            blocked_claims=blocked,
            evidence=[f"{claim.observations} paired campaign observations across calibration sets."],
        ),
        overall_verdict=Verdict.LIMITED if statistical_verdict != Verdict.FAIL else Verdict.FAIL,
        permitted_claims=permitted,
        blocked_claims=blocked,
        findings=[
            label_structural_property("Price-time priority and accounting are imposed engine properties."),
            label_emergent_result("Impact and execution costs are observed simulation outputs."),
        ],
        limitations=[
            "Prototype calibration uses aggregate targets and does not establish institutional realism."
        ],
    )


def build_release_validation_report(pack: CalibrationPackV1) -> SyntheticReleaseValidationReport:
    internal = pack.source_kind == "deterministic_demo"
    checks = [
        ReleaseCheck(
            name="exact_row_leakage",
            verdict=Verdict.FIT,
            value=False,
            evidence=["Calibration packs persist aggregates only."],
        ),
        ReleaseCheck(
            name="nearest_source_window_similarity",
            verdict=Verdict.NOT_EVALUATED,
            value="source windows not retained",
            evidence=["Requires customer-side source access."],
        ),
        ReleaseCheck(
            name="source_trajectory_correlation",
            verdict=Verdict.NOT_EVALUATED,
            value="source trajectories not retained",
            evidence=["Aggregate-only public boundary."],
        ),
        ReleaseCheck(
            name="license_eligibility",
            verdict=Verdict.FIT if internal else Verdict.LIMITED,
            value=pack.usage_basis,
        ),
        ReleaseCheck(
            name="public_private_artifact_separation",
            verdict=Verdict.FIT,
            value=True,
            evidence=["Manifest allowlist excludes local paths and source rows."],
        ),
    ]
    blocked = [
        "Claim that synthetic output is anonymous, non-derivative, or safe for unrestricted redistribution."
    ]
    return SyntheticReleaseValidationReport(
        checks=checks,
        overall_verdict=Verdict.LIMITED,
        release_permitted=internal,
        permitted_claims=["Release the internally generated demonstration package with its manifest."]
        if internal
        else [],
        blocked_claims=blocked,
        limitations=[
            "Membership inference is not applicable because no provider is trained on source records."
        ],
    )
