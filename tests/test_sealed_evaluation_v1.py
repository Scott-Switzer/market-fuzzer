from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict

import pytest

from app.evaluation.sealed_v1 import (
    AdaptiveDiagnosticResultV1,
    CampaignPolicyV1,
    FrozenStrategyArtifactV1,
    GeneratorBundleV1,
    HiddenParameterRangeV1,
    PrimaryEvaluationResultV1,
    PrimaryWorldMetricV1,
    PrimaryWorldResultV1,
    SealedCampaignEvaluatorV1,
    SealedEvaluationError,
)
from app.generators.v1 import (
    CorrelatedLatentFactorGeneratorV1,
    HeterogeneousAgentGeneratorV1,
    RegimeSwitchingPointProcessGeneratorV1,
)

SEED_MATERIAL = bytes(range(32))


def bundle() -> GeneratorBundleV1:
    return GeneratorBundleV1(
        (
            HeterogeneousAgentGeneratorV1(),
            RegimeSwitchingPointProcessGeneratorV1(),
            CorrelatedLatentFactorGeneratorV1(),
        )
    )


def policy() -> CampaignPolicyV1:
    return CampaignPolicyV1(
        same_family_ids=("heterogeneous_agent_v1", "regime_switching_point_process_v1"),
        holdout_family_ids=("correlated_latent_factor_v1",),
        worlds_per_family=2,
        hidden_parameter_ranges=(
            HiddenParameterRangeV1("heterogeneous_agent_v1", "informed_probability", 0.45, 0.7),
            HiddenParameterRangeV1("regime_switching_point_process_v1", "side_persistence", 0.4, 0.8),
            HiddenParameterRangeV1("correlated_latent_factor_v1", "factor_volatility", 1.1, 2.1),
        ),
        scoring_policy_digest="a" * 64,
    )


def finalized(artifact: bytes = b"strategy-v1"):
    evaluator = SealedCampaignEvaluatorV1()
    campaign = evaluator.prepare_campaign(
        policy=policy(), generator_bundle=bundle(), seed_material=SEED_MATERIAL
    )
    frozen = evaluator.freeze_strategy_artifact(campaign, artifact)
    return evaluator, campaign, evaluator.finalize_primary(frozen, instruments=("NOVA", "ORBIT"), steps=4)


def test_commitment_hides_seed_family_labels_and_parameter_values_until_finalization() -> None:
    evaluator = SealedCampaignEvaluatorV1()
    campaign = evaluator.prepare_campaign(
        policy=policy(), generator_bundle=bundle(), seed_material=SEED_MATERIAL
    )
    public = json.dumps(campaign.commitment.public_document, sort_keys=True)
    assert SEED_MATERIAL.hex() not in public
    assert "heterogeneous_agent_v1" not in public
    assert "factor_volatility" not in public
    assert "secret_seed_commitment" in public
    assert "hidden_family_allocation_commitment" in public
    with pytest.raises(SealedEvaluationError, match="must freeze"):
        evaluator.finalize_primary(campaign, instruments=("NOVA", "ORBIT"), steps=4)
    with pytest.raises(SealedEvaluationError, match="only be revealed"):
        evaluator.reveal(campaign)


def test_primary_world_selection_is_independent_of_strategy_artifact_and_uses_holdouts() -> None:
    evaluator = SealedCampaignEvaluatorV1()
    first = evaluator.prepare_campaign(
        policy=policy(), generator_bundle=bundle(), seed_material=SEED_MATERIAL
    )
    second = evaluator.prepare_campaign(
        policy=policy(), generator_bundle=bundle(), seed_material=SEED_MATERIAL
    )
    first = evaluator.finalize_primary(
        evaluator.freeze_strategy_artifact(first, b"strategy-a"), instruments=("NOVA", "ORBIT"), steps=4
    )
    second = evaluator.finalize_primary(
        evaluator.freeze_strategy_artifact(second, b"strategy-b"), instruments=("NOVA", "ORBIT"), steps=4
    )
    assert first.commitment == second.commitment
    assert first.finalized_primary_result is not None and second.finalized_primary_result is not None
    assert (
        first.finalized_primary_result.strategy_artifact_digest
        != second.finalized_primary_result.strategy_artifact_digest
    )
    assert first.finalized_primary_result.worlds == second.finalized_primary_result.worlds
    plans = evaluator._primary_world_plans(first)
    assert {plan.family_id for plan in plans} == {
        "heterogeneous_agent_v1",
        "regime_switching_point_process_v1",
        "correlated_latent_factor_v1",
    }
    assert len(plans) == 6


def test_hidden_parameters_change_real_generator_inputs_without_entering_primary_result() -> None:
    evaluator, _, campaign = finalized()
    plan = next(
        plan
        for plan in evaluator._primary_world_plans(campaign)
        if plan.family_id == "heterogeneous_agent_v1"
    )
    value = plan.parameter_overrides["informed_probability"]
    assert 0.45 <= value <= 0.7
    world = campaign.generator_bundle.generator_for(plan.family_id).generate(
        seed=plan.seed,
        instruments=("NOVA", "ORBIT"),
        steps=4,
        parameter_overrides=plan.parameter_overrides,
    )
    assert world.parameters["informed_probability"] == value
    assert campaign.finalized_primary_result is not None
    assert "informed_probability" not in json.dumps(asdict(campaign.finalized_primary_result))


def test_primary_metrics_bind_to_opaque_receipts_without_hidden_provenance() -> None:
    evaluator = SealedCampaignEvaluatorV1()
    campaign = evaluator.prepare_campaign(
        policy=policy(), generator_bundle=bundle(), seed_material=SEED_MATERIAL
    )
    campaign = evaluator.freeze_strategy_artifact(campaign, b"strategy-v1")
    campaign = evaluator.finalize_primary(
        campaign,
        instruments=("NOVA", "ORBIT"),
        steps=4,
        metric_evaluator=lambda observations: {"observation_count": float(len(observations))},
    )
    assert campaign.finalized_primary_result is not None
    result = campaign.finalized_primary_result
    assert len(result.metrics) == len(result.worlds)
    assert {metric.world_receipt for metric in result.metrics} == {
        world.world_receipt for world in result.worlds
    }
    assert "family_id" not in json.dumps([asdict(metric) for metric in result.metrics])
    assert result.scoring_policy_digest == policy().scoring_policy_digest


def test_primary_results_reject_duplicate_receipts_or_metric_cells() -> None:
    world = PrimaryWorldResultV1("a" * 64, 1, "b" * 64)
    with pytest.raises(SealedEvaluationError, match="unique opaque"):
        PrimaryEvaluationResultV1("c" * 64, "d" * 64, (world, world))
    metric = PrimaryWorldMetricV1(world.world_receipt, "cost", 1.0)
    with pytest.raises(SealedEvaluationError, match="one value"):
        PrimaryEvaluationResultV1("c" * 64, "d" * 64, (world,), (metric, metric))


def test_observation_projection_has_no_hidden_provenance_or_future_payload() -> None:
    evaluator, _, campaign = finalized()
    plan = evaluator._primary_world_plans(campaign)[0]
    world = campaign.generator_bundle.generator_for(plan.family_id).generate(
        seed=plan.seed,
        instruments=("NOVA", "ORBIT"),
        steps=4,
        parameter_overrides=plan.parameter_overrides,
    )
    observations = evaluator.project_observations(world)
    assert [item.exchange_time_ns for item in observations] == sorted(
        item.exchange_time_ns for item in observations
    )
    assert set(asdict(observations[0])) == {
        "sequence",
        "exchange_time_ns",
        "instrument_id",
        "kind",
        "side",
        "price_ticks",
        "quantity",
    }
    for forbidden in ("seed", "family_id", "regime", "event_id", "generator_version"):
        assert not hasattr(observations[0], forbidden)
    assert {item.kind for item in observations} == {"market_update"}


def test_finalized_campaign_reveals_a_verifiable_preimage_and_rejects_tampering() -> None:
    evaluator, _, campaign = finalized()
    reveal = evaluator.reveal(campaign)
    assert evaluator.verify_reveal(campaign.commitment, reveal)
    bad_reveal = type(reveal)(reveal.public_document, (b"x" * 32).hex(), reveal.policy_preimage)
    assert not evaluator.verify_reveal(campaign.commitment, bad_reveal)
    bad_policy = {**reveal.policy_preimage, "same_family_ids": ["correlated_latent_factor_v1"]}
    assert not evaluator.verify_reveal(
        campaign.commitment, type(reveal)(reveal.public_document, reveal.secret_seed_material_hex, bad_policy)
    )


def test_fixed_campaign_replay_is_byte_equivalent_across_processes() -> None:
    script = """
from app.evaluation.sealed_v1 import CampaignPolicyV1, GeneratorBundleV1, HiddenParameterRangeV1, SealedCampaignEvaluatorV1
from app.generators.v1 import CorrelatedLatentFactorGeneratorV1, HeterogeneousAgentGeneratorV1, RegimeSwitchingPointProcessGeneratorV1
policy = CampaignPolicyV1(
    same_family_ids=("heterogeneous_agent_v1", "regime_switching_point_process_v1"),
    holdout_family_ids=("correlated_latent_factor_v1",), worlds_per_family=2,
    hidden_parameter_ranges=(
        HiddenParameterRangeV1("heterogeneous_agent_v1", "informed_probability", 0.45, 0.7),
        HiddenParameterRangeV1("regime_switching_point_process_v1", "side_persistence", 0.4, 0.8),
        HiddenParameterRangeV1("correlated_latent_factor_v1", "factor_volatility", 1.1, 2.1),
    ), scoring_policy_digest="a" * 64,
)
evaluator = SealedCampaignEvaluatorV1()
campaign = evaluator.prepare_campaign(
    policy=policy,
    generator_bundle=GeneratorBundleV1((HeterogeneousAgentGeneratorV1(), RegimeSwitchingPointProcessGeneratorV1(), CorrelatedLatentFactorGeneratorV1())),
    seed_material=bytes(range(32)),
)
campaign = evaluator.freeze_strategy_artifact(campaign, b"strategy-v1")
campaign = evaluator.finalize_primary(campaign, instruments=("NOVA", "ORBIT"), steps=4)
print(campaign.finalized_primary_result.result_digest)
"""
    _, _, campaign = finalized()
    assert campaign.finalized_primary_result is not None
    replay_digest = subprocess.check_output([sys.executable, "-c", script], text=True).strip()
    assert replay_digest == campaign.finalized_primary_result.result_digest


def test_primary_and_adaptive_evidence_cannot_share_a_result_namespace() -> None:
    with pytest.raises(SealedEvaluationError, match="sealed-primary"):
        PrimaryEvaluationResultV1("c" * 64, "a" * 64, (), result_namespace="adaptive_diagnostic_v1")
    diagnostic = AdaptiveDiagnosticResultV1("liquidity withdrawal", "f" * 64)
    assert diagnostic.result_namespace == "adaptive_diagnostic_v1"


def test_freeze_rejects_empty_or_replaced_artifacts() -> None:
    evaluator = SealedCampaignEvaluatorV1()
    campaign = evaluator.prepare_campaign(
        policy=policy(), generator_bundle=bundle(), seed_material=SEED_MATERIAL
    )
    with pytest.raises(SealedEvaluationError, match="empty"):
        evaluator.freeze_strategy_artifact(campaign, b"")
    frozen = evaluator.freeze_strategy_artifact(campaign, b"strategy")
    with pytest.raises(SealedEvaluationError, match="already frozen"):
        evaluator.freeze_strategy_artifact(frozen, b"replacement")
    assert FrozenStrategyArtifactV1("a" * 64, 1).byte_length == 1
