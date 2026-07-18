"""Evidence manifests and fit-for-use reports for enterprise experiments."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field


class EvidenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version: Literal["1.0"] = "1.0"
    experiment_id: str
    scenario_pack_id: str
    compile_hash: str
    strategy_ids: list[str]
    seeds: list[int]
    evidence_ids: list[str]
    deterministic_authority: str = "application_simulator"
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
    report_hash: str = ""


def build_enterprise_validation_report(experiment: dict) -> EnterpriseValidationReport:
    result = experiment.get("result")
    if result is None:
        raise ValueError(
            f"Experiment {experiment['experiment_id']!r} has no completed result; cannot build validation report."
        )
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
        claim_boundary=result["claim_boundary"],
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
            if result.get("calibration_stable")
            else "LIMITED"
            if result.get("calibration_pack_id")
            else "NOT_EVALUATED",
            summary=(
                "A versioned calibration pack is attached and its held-out bootstrap stability check passed."
                if result.get("calibration_stable")
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
                "Calibration stability passed for the attached aggregate-only pack; statistical fidelity still requires held-out comparison evidence."
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
    )
    report.report_hash = hashlib.sha256(
        json.dumps(report.model_dump(mode="json", exclude={"report_hash"}), sort_keys=True).encode()
    ).hexdigest()
    return report
