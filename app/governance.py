"""Evidence manifests and fit-for-use reports for enterprise experiments."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from app.evaluation import development_fixture_evidence


class EvidenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version: Literal["1.0"] = "1.0"
    experiment_id: str
    scenario_pack_id: str
    compile_hash: str
    strategy_ids: list[str]
    seeds: list[int]
    evidence_ids: list[str]
    calibration_parameter_set_ids: list[str] = Field(default_factory=list)
    deterministic_authority: str = "application_simulator"
    evaluation_scope: Literal["development_fixture", "sealed_primary", "adaptive_diagnostic"]
    evaluation_evidence_digest: str
    claim_boundary: str


class FitForUseVector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal[
        "mechanical_validity",
        "calibration_stability",
        "statistical_fidelity",
        "interventional_fidelity",
        "downstream_utility",
    ]
    verdict: Literal["FIT", "LIMITED", "FAIL", "NOT_EVALUATED"]
    summary: str
    evidence_ids: list[str] = Field(default_factory=list)


class EnsembleMetricSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Literal["filled_quantity", "implementation_shortfall_bps", "completion_pct"]
    mean: float
    p05: float
    p95: float
    observations: int = Field(ge=1)


class CalibrationEnsembleSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameter_set_count: int = Field(ge=0)
    world_count: int = Field(ge=0)
    metrics: list[EnsembleMetricSummary] = Field(default_factory=list)


class EnterpriseValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_version: Literal["1.0"] = "1.0"
    experiment_id: str
    evidence_manifest: EvidenceManifest
    vectors: list[FitForUseVector] = Field(min_length=5, max_length=5)
    overall_verdict: Literal["FIT", "LIMITED", "FAIL", "NOT_EVALUATED"]
    permitted_claims: list[str]
    blocked_claims: list[str]
    limitations: list[str]
    calibration_ensemble: CalibrationEnsembleSummary
    report_hash: str = ""


def build_enterprise_validation_report(experiment: dict) -> EnterpriseValidationReport:
    result = experiment.get("result")
    if result is None:
        raise ValueError(
            f"Experiment {experiment['experiment_id']!r} has no completed result; cannot build validation report."
        )
    evaluation_evidence = _evaluation_evidence(result)
    evidence_ids = [
        "evidence:"
        + hashlib.sha256(f"{experiment['experiment_id']}:{index}:{row['policy_id']}".encode()).hexdigest()[
            :16
        ]
        for index, row in enumerate(result.get("strategy_results", []))
    ]
    manifest = EvidenceManifest(
        experiment_id=experiment["experiment_id"],
        scenario_pack_id=experiment["scenario_pack_id"],
        compile_hash=result["compile_hash"],
        strategy_ids=experiment["strategy_ids"],
        seeds=experiment["seeds"],
        evidence_ids=evidence_ids,
        calibration_parameter_set_ids=[
            item["parameter_set_id"] for item in result.get("calibration_ensemble", [])
        ],
        evaluation_scope=cast(
            Literal["development_fixture", "sealed_primary", "adaptive_diagnostic"],
            evaluation_evidence["scope"],
        ),
        evaluation_evidence_digest=str(evaluation_evidence["evidence_digest"]),
        claim_boundary=result["claim_boundary"],
    )
    ensemble_runs = result.get("calibration_ensemble_runs", [])
    ensemble_metrics: list[EnsembleMetricSummary] = []
    for metric in ("filled_quantity", "implementation_shortfall_bps", "completion_pct"):
        values = [
            float(world[metric])
            for run in ensemble_runs
            for world in run.get("world_results", [])
            if metric in world
        ]
        if values:
            ordered = sorted(values)
            ensemble_metrics.append(
                EnsembleMetricSummary(
                    metric=metric,
                    mean=sum(values) / len(values),
                    p05=ordered[max(0, int((len(ordered) - 1) * 0.05))],
                    p95=ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.95))],
                    observations=len(values),
                )
            )
    ensemble_summary = CalibrationEnsembleSummary(
        parameter_set_count=len(ensemble_runs),
        world_count=sum(len(run.get("world_results", [])) for run in ensemble_runs),
        metrics=ensemble_metrics,
    )
    vectors = [
        FitForUseVector(
            name="mechanical_validity",
            verdict="FIT",
            summary="The experiment references a deterministic compiled scenario and immutable result rows.",
        ),
        FitForUseVector(
            name="calibration_stability",
            verdict="FIT"
            if result.get("calibration_stable") and ensemble_summary.world_count > 0
            else "LIMITED"
            if result.get("calibration_pack_id")
            else "NOT_EVALUATED",
            summary=(
                "A versioned calibration pack is attached, its held-out bootstrap stability check passed, and ensemble worlds produced uncertainty metrics."
                if result.get("calibration_stable") and ensemble_summary.world_count > 0
                else "A versioned calibration pack is attached; its held-out bootstrap stability check did not pass."
                if result.get("calibration_pack_id")
                else "No calibration pack is attached to this experiment."
            ),
        ),
        FitForUseVector(
            name="statistical_fidelity",
            verdict="NOT_EVALUATED",
            summary="Statistical fidelity requires a declared reference calibration pack.",
        ),
        FitForUseVector(
            name="interventional_fidelity",
            verdict="FIT" if evidence_ids else "FAIL",
            summary="The declared scenario pack produced deterministic strategy evidence."
            if evidence_ids
            else "No strategy evidence was produced.",
            evidence_ids=evidence_ids,
        ),
        FitForUseVector(
            name="downstream_utility",
            verdict="LIMITED",
            summary="The current experiment supports bounded execution stress testing only.",
            evidence_ids=evidence_ids,
        ),
    ]
    verdicts = {vector.verdict for vector in vectors}
    overall_verdict = (
        "FAIL"
        if "FAIL" in verdicts
        else "LIMITED"
        if "LIMITED" in verdicts or "NOT_EVALUATED" in verdicts
        else "FIT"
    )
    report = EnterpriseValidationReport(
        experiment_id=experiment["experiment_id"],
        evidence_manifest=manifest,
        vectors=vectors,
        overall_verdict=cast(Literal["FIT", "LIMITED", "FAIL", "NOT_EVALUATED"], overall_verdict),
        permitted_claims=["Compare registered strategies inside the declared synthetic scenario pack."],
        blocked_claims=["Claim live-market fidelity, profitability, production capacity, or best execution."],
        limitations=(
            [
                "Calibration stability and ensemble execution passed for the attached aggregate-only pack; statistical fidelity still requires held-out comparison evidence."
            ]
            if result.get("calibration_stable")
            else [
                "Calibration was attached but its held-out stability check did not pass; statistical fidelity remains unevaluated."
            ]
            if result.get("calibration_pack_id")
            else [
                "Calibration and statistical fidelity were not evaluated because no reference pack was attached."
            ]
        ),
        calibration_ensemble=ensemble_summary,
    )
    report.report_hash = hashlib.sha256(
        json.dumps(report.model_dump(mode="json", exclude={"report_hash"}), sort_keys=True).encode()
    ).hexdigest()
    return report


def _evaluation_evidence(result: dict[str, Any]) -> dict[str, Any]:
    """Read the shared envelope, safely labelling historical deterministic results."""
    evidence = result.get("evaluation_evidence")
    required = {"scope", "evidence_digest", "claim_boundary"}
    if isinstance(evidence, dict) and required.issubset(evidence):
        return evidence
    return development_fixture_evidence(
        payload=result,
        limitation="This historical Stress Lab result predates the shared evidence envelope.",
    ).to_dict()
