import pytest
from pydantic import ValidationError

from app.validation import (
    EMERGENT_LABEL,
    STRUCTURAL_LABEL,
    MetricEvidence,
    ReleaseCheck,
    SimulatorValidationReport,
    SyntheticReleaseValidationReport,
    UseCaseVerdict,
    ValidationVector,
    Verdict,
    assert_exact_evidence_label,
    label_emergent_result,
    label_structural_property,
)

VECTOR_NAMES = (
    "mechanical_validity",
    "calibration_stability",
    "statistical_fidelity",
    "interventional_fidelity",
    "downstream_utility",
)


def _vector(name: str) -> ValidationVector:
    return ValidationVector(
        name=name,
        verdict=Verdict.LIMITED,
        metrics=[MetricEvidence(name=f"{name}_check", target="documented", verdict=Verdict.LIMITED)],
        summary="bounded evidence",
    )


def test_simulator_report_requires_exact_five_vectors_and_claim_lists():
    report = SimulatorValidationReport(
        vectors=[_vector(name) for name in VECTOR_NAMES],
        use_case=UseCaseVerdict(
            verdict=Verdict.LIMITED,
            permitted_claims=["Run stress tests."],
            blocked_claims=["Estimate production capacity."],
        ),
        overall_verdict=Verdict.LIMITED,
        permitted_claims=["Run stress tests."],
        blocked_claims=["Estimate production capacity."],
    )
    assert report.use_case.use_case == "execution_stress_testing"
    with pytest.raises(ValidationError, match="at least 5 items"):
        SimulatorValidationReport(
            vectors=[_vector(name) for name in VECTOR_NAMES[:4]],
            use_case=report.use_case,
            overall_verdict=Verdict.LIMITED,
            permitted_claims=[],
            blocked_claims=[],
        )


def test_release_report_has_exact_risk_checks_and_membership_not_applicable():
    names = (
        "exact_row_leakage",
        "nearest_source_window_similarity",
        "source_trajectory_correlation",
        "license_eligibility",
        "public_private_artifact_separation",
    )
    report = SyntheticReleaseValidationReport(
        checks=[ReleaseCheck(name=name, verdict=Verdict.FIT) for name in names],
        overall_verdict=Verdict.FIT,
        release_permitted=True,
        permitted_claims=["Release demo package."],
        blocked_claims=["Claim anonymous output."],
    )
    assert report.category == "confidentiality_and_derivation_risk"
    assert report.membership_inference == "NOT_APPLICABLE"


def test_structural_and_emergent_labels_are_exact_regression_contract():
    structural = label_structural_property("FIFO is enforced by matching code")
    emergent = label_emergent_result("This seeded run exhibited heavy tails")
    assert structural.label.value == STRUCTURAL_LABEL == "structural"
    assert emergent.label.value == EMERGENT_LABEL == "emergent"
    assert assert_exact_evidence_label("structural").value == "structural"
    with pytest.raises(ValueError, match="must be exactly"):
        assert_exact_evidence_label("guaranteed")
