import pytest

from app.evaluation import (
    EvaluationEvidenceV1,
    EvidenceScopeV1,
    adaptive_diagnostic_evidence,
    development_fixture_evidence,
    sealed_primary_evidence,
)
from app.evaluation.sealed_v1 import (
    CampaignPolicyV1,
    GeneratorBundleV1,
    HiddenParameterRangeV1,
    SealedCampaignEvaluatorV1,
    SealedEvaluationError,
)
from app.generators.v1 import HeterogeneousAgentGeneratorV1, RegimeSwitchingPointProcessGeneratorV1


def test_development_and_adaptive_scopes_cannot_claim_sealed_provenance() -> None:
    development = development_fixture_evidence(payload={"seed": 42}, limitation="fixed fixture")
    adaptive = adaptive_diagnostic_evidence(
        payload={"failure": "participation"}, mechanism="participation", limitation="strategy-aware"
    )
    assert development.scope == EvidenceScopeV1.DEVELOPMENT
    assert adaptive.scope == EvidenceScopeV1.ADAPTIVE_DIAGNOSTIC
    with pytest.raises(SealedEvaluationError, match="development fixtures"):
        EvaluationEvidenceV1(
            EvidenceScopeV1.DEVELOPMENT,
            "a" * 64,
            None,
            "b" * 64,
            "bad",
            ("bad",),
        )
    with pytest.raises(SealedEvaluationError, match="development fixtures"):
        EvaluationEvidenceV1(
            EvidenceScopeV1.DEVELOPMENT,
            None,
            None,
            "b" * 64,
            "bad",
            ("bad",),
            mechanism="strategy-aware",
        )


def test_only_finalized_campaign_constructs_sealed_primary_evidence() -> None:
    evaluator = SealedCampaignEvaluatorV1()
    campaign = evaluator.prepare_campaign(
        policy=CampaignPolicyV1(
            same_family_ids=("heterogeneous_agent_v1",),
            holdout_family_ids=("regime_switching_point_process_v1",),
            worlds_per_family=1,
            hidden_parameter_ranges=(
                HiddenParameterRangeV1("heterogeneous_agent_v1", "informed_probability", 0.5, 0.6),
            ),
            scoring_policy_digest="a" * 64,
        ),
        generator_bundle=GeneratorBundleV1(
            (HeterogeneousAgentGeneratorV1(), RegimeSwitchingPointProcessGeneratorV1())
        ),
        seed_material=bytes(range(32)),
    )
    with pytest.raises(SealedEvaluationError, match="finalized campaign"):
        sealed_primary_evidence(campaign)
    campaign = evaluator.freeze_strategy_artifact(campaign, b"strategy")
    campaign = evaluator.finalize_primary(campaign, instruments=("NOVA",), steps=2)
    assert campaign.finalized_primary_result is not None
    evidence = sealed_primary_evidence(campaign)
    assert evidence.scope == EvidenceScopeV1.SEALED_PRIMARY
    assert evidence.result_digest == campaign.finalized_primary_result.result_digest
    assert evidence.to_dict()["evidence_digest"] == evidence.evidence_digest


def test_primary_scope_rejects_adaptive_fields() -> None:
    with pytest.raises(SealedEvaluationError, match="primary evidence"):
        EvaluationEvidenceV1(
            EvidenceScopeV1.SEALED_PRIMARY,
            "a" * 64,
            "b" * 64,
            "c" * 64,
            "bad",
            ("bounded",),
            mechanism="adaptive",
        )
