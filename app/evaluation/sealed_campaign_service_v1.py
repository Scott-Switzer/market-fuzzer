"""Server-side lifecycle for V2 sealed campaigns.

The store retains secret seed material and hidden policy ranges.  Public methods
return only the pre-commitment document until evaluation has finalized.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any, Protocol, cast

from app.execution_store import ArenaStore
from app.generators.v1 import (
    CorrelatedLatentFactorGeneratorV1,
    HeterogeneousAgentGeneratorV1,
    RegimeSwitchingPointProcessGeneratorV1,
)
from app.strategy_lab import ExternalAdapterContract
from app.strategy_runtime import ContainerStrategyArtifactV1, ContainerStreamingStrategySessionV1

from .sealed_v1 import (
    CampaignPolicyV1,
    GeneratorBundleV1,
    HiddenParameterRangeV1,
    PreparedCampaignV1,
    SealedCampaignEvaluatorV1,
    SealedEvaluationError,
)
from .v2_runner import IsolatedSealedV2WorldRunnerV1, StrategyDecisionPortV1


class SealedCampaignServiceError(ValueError):
    """Raised when a registered artifact cannot enter the sealed V2 lifecycle."""


class SessionFactoryV1(Protocol):
    def __call__(self, artifact: ContainerStrategyArtifactV1) -> StrategyDecisionPortV1: ...


def default_generator_bundle_v1() -> GeneratorBundleV1:
    """The pinned initial multi-family bundle; its digest is committed before freeze."""
    return GeneratorBundleV1(
        (
            HeterogeneousAgentGeneratorV1(),
            RegimeSwitchingPointProcessGeneratorV1(),
            CorrelatedLatentFactorGeneratorV1(),
        )
    )


class SealedCampaignServiceV1:
    """Coordinates persistent commit, freeze, V2 execution, finalization, and reveal."""

    def __init__(
        self,
        store: ArenaStore,
        *,
        evaluator: SealedCampaignEvaluatorV1 | None = None,
        generator_bundle: GeneratorBundleV1 | None = None,
        session_factory: SessionFactoryV1 | None = None,
    ) -> None:
        self.store = store
        self.evaluator = evaluator or SealedCampaignEvaluatorV1()
        self.generator_bundle = generator_bundle or default_generator_bundle_v1()
        self.session_factory = session_factory or self._container_session

    def prepare(
        self,
        *,
        campaign_id: str,
        strategy_id: str,
        policy: CampaignPolicyV1,
        instruments: tuple[str, ...],
        steps: int,
        actor: str,
        seed_material: bytes | None = None,
    ) -> dict[str, Any]:
        # Reject legacy or non-isolated registrations before publishing a commitment.
        self._artifact_for_strategy(strategy_id)
        campaign = self.evaluator.prepare_campaign(
            policy=policy, generator_bundle=self.generator_bundle, seed_material=seed_material
        )
        self.store.create_sealed_campaign(
            campaign_id=campaign_id,
            strategy_id=strategy_id,
            public_document=campaign.commitment.public_document,
            commitment_digest=campaign.commitment.commitment_digest,
            policy=self._policy_dict(policy),
            generator_bundle_digest=self.generator_bundle.digest,
            secret_seed_material_hex=campaign._secret_seed_material.hex(),
            instruments=instruments,
            steps=steps,
            actor=actor,
        )
        return self.store.sealed_campaign(campaign_id)

    def freeze(self, campaign_id: str, *, actor: str) -> dict[str, Any]:
        row = self.store.sealed_campaign_private(campaign_id)
        if row["state"] != "prepared":
            raise SealedCampaignServiceError("sealed campaign is not ready to freeze")
        artifact = self._artifact_for_strategy(str(row["strategy_id"]))
        return self.store.freeze_sealed_campaign(
            campaign_id,
            artifact_digest=artifact.artifact_digest,
            artifact_byte_length=len(artifact.canonical_bytes),
            actor=actor,
        )

    def finalize(self, campaign_id: str, *, actor: str) -> dict[str, Any]:
        row = self.store.sealed_campaign_private(campaign_id)
        if row["state"] != "frozen":
            raise SealedCampaignServiceError("sealed campaign is not ready to finalize")
        campaign = self._rehydrate(row)
        if campaign.artifact is None:
            raise SealedCampaignServiceError("frozen campaign has no immutable artifact")
        artifact = self._artifact_for_strategy(str(row["strategy_id"]))
        if artifact.artifact_digest != campaign.artifact.digest:
            raise SealedCampaignServiceError("registered strategy artifact changed after campaign freeze")
        runner = IsolatedSealedV2WorldRunnerV1(lambda: self.session_factory(artifact))
        final = self.evaluator.finalize_primary(
            campaign,
            instruments=tuple(row["instruments"]),
            steps=int(row["steps"]),
            world_runner=runner,
        )
        assert final.finalized_primary_result is not None
        return self.store.finalize_sealed_campaign(
            campaign_id, result=asdict(final.finalized_primary_result), actor=actor
        )

    def reveal(self, campaign_id: str) -> dict[str, Any]:
        row = self.store.sealed_campaign_private(campaign_id)
        if row["state"] != "finalized" or row["result"] is None:
            raise SealedCampaignServiceError("campaign may reveal only after primary finalization")
        campaign = self._rehydrate(row, restore_artifact=False)
        # The evaluator only needs a finalized marker before it will disclose the preimage.
        final = replace(campaign, finalized_primary_result=cast(Any, row["result"]))
        reveal = self.evaluator.reveal(final)
        if not self.evaluator.verify_reveal(campaign.commitment, reveal):
            raise SealedCampaignServiceError("stored campaign reveal no longer verifies its commitment")
        return asdict(reveal)

    def _rehydrate(self, row: dict[str, Any], *, restore_artifact: bool = True) -> PreparedCampaignV1:
        if row["generator_bundle_digest"] != self.generator_bundle.digest:
            raise SealedCampaignServiceError(
                "campaign generator bundle is not available at its committed digest"
            )
        policy = self._policy_from_dict(row["policy"])
        try:
            campaign = self.evaluator.prepare_campaign(
                policy=policy,
                generator_bundle=self.generator_bundle,
                seed_material=bytes.fromhex(str(row["secret_seed_material_hex"])),
            )
        except (TypeError, ValueError, SealedEvaluationError) as error:
            raise SealedCampaignServiceError("persisted sealed campaign is invalid") from error
        if campaign.commitment.commitment_digest != row["commitment_digest"]:
            raise SealedCampaignServiceError("persisted sealed campaign commitment does not verify")
        if restore_artifact and row["state"] in {"frozen", "finalized"}:
            if not row["artifact_digest"] or row["artifact_byte_length"] is None:
                raise SealedCampaignServiceError("frozen campaign is missing artifact provenance")
            campaign = self.evaluator.freeze_strategy_artifact(
                campaign, self._artifact_for_strategy(str(row["strategy_id"])).canonical_bytes
            )
            if campaign.artifact is None or campaign.artifact.digest != row["artifact_digest"]:
                raise SealedCampaignServiceError("persisted frozen artifact digest does not verify")
        return campaign

    def _container_session(
        self, artifact: ContainerStrategyArtifactV1
    ) -> ContainerStreamingStrategySessionV1:
        return ContainerStreamingStrategySessionV1(
            artifact,
            response_recorder=self.store.record_strategy_response,
            response_lookup=self.store.find_strategy_response,
        )

    def _artifact_for_strategy(self, strategy_id: str) -> ContainerStrategyArtifactV1:
        strategy = self.store.strategy(strategy_id)
        contract_data = strategy.get("external_adapter")
        if not isinstance(contract_data, dict):
            raise SealedCampaignServiceError("sealed V2 campaigns require a registered container artifact")
        contract = ExternalAdapterContract.model_validate(contract_data)
        if (
            contract.adapter_id != "container_jsonl_v1"
            or contract.input_observation_schema != "market_observation_v2"
            or contract.output_action_schema != "execution_action_v2"
        ):
            raise SealedCampaignServiceError("sealed V2 campaigns require a V2 container strategy contract")
        return ContainerStrategyArtifactV1(
            image_digest=str(contract.image_digest),
            command=tuple(contract.command or ()),
            timeout_ms=contract.timeout_ms,
        )

    @staticmethod
    def _policy_dict(policy: CampaignPolicyV1) -> dict[str, Any]:
        return {
            "same_family_ids": list(policy.same_family_ids),
            "holdout_family_ids": list(policy.holdout_family_ids),
            "worlds_per_family": policy.worlds_per_family,
            "hidden_parameter_ranges": [asdict(item) for item in policy.hidden_parameter_ranges],
            "scoring_policy_digest": policy.scoring_policy_digest,
            "campaign_policy_version": policy.campaign_policy_version,
        }

    @staticmethod
    def _policy_from_dict(value: dict[str, Any]) -> CampaignPolicyV1:
        return CampaignPolicyV1(
            same_family_ids=tuple(value["same_family_ids"]),
            holdout_family_ids=tuple(value["holdout_family_ids"]),
            worlds_per_family=int(value["worlds_per_family"]),
            hidden_parameter_ranges=tuple(
                HiddenParameterRangeV1(**item) for item in value["hidden_parameter_ranges"]
            ),
            scoring_policy_digest=str(value["scoring_policy_digest"]),
            campaign_policy_version=str(value.get("campaign_policy_version", "sealed-campaign-v1")),
        )
