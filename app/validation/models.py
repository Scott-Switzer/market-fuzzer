from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .labeling import LabeledFinding


class Verdict(StrEnum):
    FIT = "FIT"
    LIMITED = "LIMITED"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT_EVALUATED"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetricEvidence(StrictModel):
    name: str
    value: float | int | bool | str | None = None
    target: str
    verdict: Verdict
    evidence: list[str] = Field(default_factory=list)
    limitation: str | None = None


class ValidationVector(StrictModel):
    name: Literal[
        "mechanical_validity",
        "calibration_stability",
        "statistical_fidelity",
        "interventional_fidelity",
        "downstream_utility",
    ]
    verdict: Verdict
    metrics: list[MetricEvidence]
    summary: str


class UseCaseVerdict(StrictModel):
    use_case: Literal["execution_stress_testing"] = "execution_stress_testing"
    verdict: Verdict
    permitted_claims: list[str]
    blocked_claims: list[str]
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def claims_are_disjoint(self) -> UseCaseVerdict:
        if set(self.permitted_claims) & set(self.blocked_claims):
            raise ValueError("permitted and blocked claims must be disjoint")
        return self


class SimulatorValidationReport(StrictModel):
    report_version: Literal["1.0"] = "1.0"
    vectors: list[ValidationVector] = Field(min_length=5, max_length=5)
    use_case: UseCaseVerdict
    overall_verdict: Verdict
    permitted_claims: list[str]
    blocked_claims: list[str]
    findings: list[LabeledFinding] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def exact_vectors(self) -> SimulatorValidationReport:
        expected = {
            "mechanical_validity",
            "calibration_stability",
            "statistical_fidelity",
            "interventional_fidelity",
            "downstream_utility",
        }
        if {vector.name for vector in self.vectors} != expected:
            raise ValueError("report must contain the five exact validation vectors")
        return self


class ReleaseCheck(StrictModel):
    name: Literal[
        "exact_row_leakage",
        "nearest_source_window_similarity",
        "source_trajectory_correlation",
        "license_eligibility",
        "public_private_artifact_separation",
    ]
    verdict: Verdict
    value: float | bool | str | None = None
    evidence: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class SyntheticReleaseValidationReport(StrictModel):
    report_version: Literal["1.0"] = "1.0"
    category: Literal["confidentiality_and_derivation_risk"] = "confidentiality_and_derivation_risk"
    checks: list[ReleaseCheck] = Field(min_length=5, max_length=5)
    overall_verdict: Verdict
    release_permitted: bool
    membership_inference: Literal["NOT_APPLICABLE"] = "NOT_APPLICABLE"
    permitted_claims: list[str]
    blocked_claims: list[str]
    limitations: list[str] = Field(default_factory=list)
