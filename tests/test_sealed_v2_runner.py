from __future__ import annotations

import hashlib
import json

import pytest

from app.evaluation.sealed_v1 import (
    CampaignPolicyV1,
    GeneratorBundleV1,
    HiddenParameterRangeV1,
    SealedCampaignEvaluatorV1,
    SealedEvaluationError,
)
from app.evaluation.v2_runner import SealedV2RunnerError, SealedV2WorldRunnerV1
from app.exchange.v2 import RunManifestV2
from app.generators.v1 import HeterogeneousAgentGeneratorV1, RegimeSwitchingPointProcessGeneratorV1
from app.strategy_protocol import StrategyActionV1
from app.strategy_runtime import StrategyResponseRecordV1


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class _ReactivePort:
    def __init__(self, artifact_digest: str) -> None:
        self.artifact_digest = artifact_digest
        self.observations: list[dict] = []

    def decide(self, observation: dict) -> StrategyResponseRecordV1:
        self.observations.append(observation)
        action = StrategyActionV1(
            action_type="market",
            side="sell" if observation["side"] == "buy" else "buy",
            quantity=10,
        ).model_dump(mode="json")
        request_digest = _digest(observation)
        return StrategyResponseRecordV1(
            _digest({"artifact": self.artifact_digest, "request": request_digest}),
            self.artifact_digest,
            request_digest,
            _digest(action),
            action,
        )


def _world():
    return HeterogeneousAgentGeneratorV1().generate(seed=9, instruments=("NOVA",), steps=4)


def _manifest(artifact_digest: str) -> RunManifestV2:
    return RunManifestV2("spec", artifact_digest, "generator", "campaign", "seed")


def test_v2_runner_is_deterministic_and_exposes_only_strategy_protocol_fields() -> None:
    artifact = "a" * 64
    first_port = _ReactivePort(artifact)
    first = SealedV2WorldRunnerV1(first_port).run(_world(), _manifest(artifact))
    second = SealedV2WorldRunnerV1(_ReactivePort(artifact)).run(_world(), _manifest(artifact))
    assert first == second
    assert first.metrics["strategy_filled_quantity"] > 0
    assert len(first.ledger_digest) == 64
    assert len(first.response_journal_digest) == 64
    assert set(first_port.observations[0]) == {
        "schema_version",
        "session_id",
        "step",
        "symbol",
        "side",
        "mid_ticks",
        "best_bid_ticks",
        "best_ask_ticks",
        "spread_bps",
        "observed_volume",
        "inventory",
        "remaining_quantity",
        "exchange_latency_profile",
        "intervention_active",
    }
    serialized = json.dumps(first_port.observations)
    for hidden in ("seed", "family", "regime", "generator", "ledger", "receipt"):
        assert hidden not in serialized


def test_v2_runner_rejects_response_records_not_bound_to_frozen_artifact() -> None:
    port = _ReactivePort("b" * 64)
    with pytest.raises(SealedV2RunnerError, match="does not bind"):
        SealedV2WorldRunnerV1(port).run(_world(), _manifest("a" * 64))


def test_sealed_campaign_binds_v2_ledger_metrics_and_disallows_callback_mixing() -> None:
    artifact_bytes = b"sealed-test-artifact"
    artifact_digest = hashlib.sha256(artifact_bytes).hexdigest()
    evaluator = SealedCampaignEvaluatorV1()
    campaign = evaluator.prepare_campaign(
        policy=CampaignPolicyV1(
            same_family_ids=("heterogeneous_agent_v1",),
            holdout_family_ids=("regime_switching_point_process_v1",),
            worlds_per_family=1,
            hidden_parameter_ranges=(
                HiddenParameterRangeV1("heterogeneous_agent_v1", "informed_probability", 0.4, 0.7),
            ),
            scoring_policy_digest="c" * 64,
        ),
        generator_bundle=GeneratorBundleV1(
            (HeterogeneousAgentGeneratorV1(), RegimeSwitchingPointProcessGeneratorV1())
        ),
        seed_material=bytes(range(32)),
    )
    frozen = evaluator.freeze_strategy_artifact(campaign, artifact_bytes)
    runner = SealedV2WorldRunnerV1(_ReactivePort(artifact_digest))
    with pytest.raises(SealedEvaluationError, match="either a legacy"):
        evaluator.finalize_primary(
            frozen,
            instruments=("NOVA",),
            steps=4,
            metric_evaluator=lambda _: {"fixture": 1.0},
            world_runner=runner,
        )
    finalized = evaluator.finalize_primary(frozen, instruments=("NOVA",), steps=4, world_runner=runner)
    assert finalized.finalized_primary_result is not None
    result = finalized.finalized_primary_result
    assert all(world.execution_ledger_digest is not None for world in result.worlds)
    assert all(world.strategy_response_journal_digest is not None for world in result.worlds)
    assert len(result.metrics) == len(result.worlds) * 7
    assert "family" not in json.dumps(result.__dict__ if hasattr(result, "__dict__") else str(result))
