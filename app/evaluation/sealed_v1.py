"""Commit-reveal protocol for independent, sealed primary worlds.

This module creates and verifies canonical campaign commitments.  It deliberately
does not execute customer code: M8 supplies the isolated strategy runner.  The
only strategy-facing representation here is ``SealedObservationV1``; it omits
world identity, seeds, family labels, generator parameters, regimes, and future
events.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import string
from dataclasses import asdict, dataclass, replace
from typing import Any

from app.generators.v1 import GeneratedWorldV1, WorldGeneratorV1


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _is_digest(value: str) -> bool:
    return len(value) == 64 and all(character in string.hexdigits for character in value)


class SealedEvaluationError(ValueError):
    """Raised for invalid campaign state or an invalid sealed-evaluation input."""


@dataclass(frozen=True, slots=True)
class HiddenParameterRangeV1:
    """Private range from which a hidden world parameter is deterministically sampled."""

    family_id: str
    parameter_name: str
    lower_bound: float
    upper_bound: float

    def __post_init__(self) -> None:
        if not self.family_id or not self.parameter_name:
            raise SealedEvaluationError("hidden parameter ranges require family and parameter names")
        if not math.isfinite(self.lower_bound) or not math.isfinite(self.upper_bound):
            raise SealedEvaluationError("hidden parameter range bounds must be finite")
        if self.lower_bound >= self.upper_bound:
            raise SealedEvaluationError("hidden parameter range lower_bound must be below upper_bound")

    @property
    def commitment(self) -> str:
        return _digest(asdict(self))


@dataclass(frozen=True, slots=True)
class CampaignPolicyV1:
    """Private campaign policy; only its public commitment document is published."""

    same_family_ids: tuple[str, ...]
    holdout_family_ids: tuple[str, ...]
    worlds_per_family: int
    hidden_parameter_ranges: tuple[HiddenParameterRangeV1, ...]
    scoring_policy_digest: str
    campaign_policy_version: str = "sealed-campaign-v1"

    def __post_init__(self) -> None:
        same, holdout = set(self.same_family_ids), set(self.holdout_family_ids)
        if not same or not holdout or same & holdout:
            raise SealedEvaluationError("same-family and holdout families must be non-empty and disjoint")
        if len(same) != len(self.same_family_ids) or len(holdout) != len(self.holdout_family_ids):
            raise SealedEvaluationError("campaign family IDs must not repeat")
        if self.worlds_per_family < 1 or not _is_digest(self.scoring_policy_digest):
            raise SealedEvaluationError("campaign requires worlds and a committed scoring policy")
        family_ids = same | holdout
        if any(item.family_id not in family_ids for item in self.hidden_parameter_ranges):
            raise SealedEvaluationError("hidden parameter range references an undeclared family")
        range_keys = {(item.family_id, item.parameter_name) for item in self.hidden_parameter_ranges}
        if len(range_keys) != len(self.hidden_parameter_ranges):
            raise SealedEvaluationError("hidden parameter ranges must not repeat a family parameter")

    def public_document(self) -> dict[str, Any]:
        """Return the preimage fields that do not disclose secret values or family labels."""
        return {
            "campaign_policy_version": self.campaign_policy_version,
            "family_allocation": {
                "same_family_world_count": len(self.same_family_ids) * self.worlds_per_family,
                "family_holdout_world_count": len(self.holdout_family_ids) * self.worlds_per_family,
            },
            "hidden_family_allocation_commitment": _digest(
                {
                    "same_family_ids": sorted(self.same_family_ids),
                    "holdout_family_ids": sorted(self.holdout_family_ids),
                }
            ),
            "hidden_parameter_range_commitments": sorted(
                item.commitment for item in self.hidden_parameter_ranges
            ),
            "scoring_policy_digest": self.scoring_policy_digest,
        }

    def reveal_preimage(self) -> dict[str, Any]:
        """Return concealed allocation and parameter ranges only after finalization."""
        return {
            "same_family_ids": sorted(self.same_family_ids),
            "holdout_family_ids": sorted(self.holdout_family_ids),
            "hidden_parameter_ranges": [
                asdict(item)
                for item in sorted(
                    self.hidden_parameter_ranges, key=lambda item: (item.family_id, item.parameter_name)
                )
            ],
        }


@dataclass(frozen=True, slots=True)
class GeneratorBundleV1:
    """Version-pinned interpretable generator bundle used by a campaign."""

    generators: tuple[WorldGeneratorV1, ...]

    def __post_init__(self) -> None:
        descriptors = [(generator.family_id, generator.generator_version) for generator in self.generators]
        if not descriptors or len({family for family, _ in descriptors}) != len(descriptors):
            raise SealedEvaluationError("generator bundles require unique family IDs")

    @property
    def digest(self) -> str:
        return _digest(
            [
                {"family_id": generator.family_id, "generator_version": generator.generator_version}
                for generator in sorted(self.generators, key=lambda item: item.family_id)
            ]
        )

    def generator_for(self, family_id: str) -> WorldGeneratorV1:
        for generator in self.generators:
            if generator.family_id == family_id:
                return generator
        raise SealedEvaluationError(f"campaign references unavailable generator family: {family_id}")


@dataclass(frozen=True, slots=True)
class CampaignCommitmentV1:
    """The public, immutable commitment published before strategy freeze closes."""

    public_document: dict[str, Any]
    commitment_digest: str

    def __post_init__(self) -> None:
        if self.commitment_digest != _digest(self.public_document):
            raise SealedEvaluationError("campaign commitment digest does not match its public document")


@dataclass(frozen=True, slots=True)
class FrozenStrategyArtifactV1:
    """Content-addressed strategy artifact reference; bytes stay outside the campaign manifest."""

    digest: str
    byte_length: int

    def __post_init__(self) -> None:
        if not _is_digest(self.digest) or self.byte_length < 0:
            raise SealedEvaluationError("invalid frozen strategy artifact")


@dataclass(frozen=True, slots=True)
class SealedObservationV1:
    """One current-time observation, stripped of all hidden world provenance."""

    sequence: int
    exchange_time_ns: int
    instrument_id: str
    kind: str
    side: str | None
    price_ticks: int
    quantity: int


@dataclass(frozen=True, slots=True)
class PrimaryWorldResultV1:
    """Opaque evidence for one primary world; it intentionally omits family and seed."""

    world_receipt: str
    observation_count: int
    observation_digest: str


@dataclass(frozen=True, slots=True)
class PrimaryEvaluationResultV1:
    """Finalized primary-run evidence, kept distinct from adaptive diagnostics."""

    campaign_commitment_digest: str
    strategy_artifact_digest: str
    worlds: tuple[PrimaryWorldResultV1, ...]
    result_namespace: str = "sealed_primary_v1"

    def __post_init__(self) -> None:
        if (
            self.result_namespace != "sealed_primary_v1"
            or not self.worlds
            or not _is_digest(self.campaign_commitment_digest)
            or not _is_digest(self.strategy_artifact_digest)
        ):
            raise SealedEvaluationError("primary results require sealed-primary evidence")

    @property
    def result_digest(self) -> str:
        return _digest(asdict(self))


@dataclass(frozen=True, slots=True)
class AdaptiveDiagnosticResultV1:
    """Strategy-aware failure search result that cannot be substituted for a primary score."""

    mechanism: str
    minimized_reproducer_digest: str
    result_namespace: str = "adaptive_diagnostic_v1"

    def __post_init__(self) -> None:
        if (
            self.result_namespace != "adaptive_diagnostic_v1"
            or not self.mechanism
            or not _is_digest(self.minimized_reproducer_digest)
        ):
            raise SealedEvaluationError("adaptive diagnostics require their own evidence namespace")


@dataclass(frozen=True, slots=True)
class CampaignRevealV1:
    """Post-finalization preimage used to verify a published campaign commitment."""

    public_document: dict[str, Any]
    secret_seed_material_hex: str
    policy_preimage: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedCampaignV1:
    """Evaluator-owned campaign state. Do not expose this object to a strategy session."""

    commitment: CampaignCommitmentV1
    policy: CampaignPolicyV1
    generator_bundle: GeneratorBundleV1
    _secret_seed_material: bytes
    artifact: FrozenStrategyArtifactV1 | None = None
    finalized_primary_result: PrimaryEvaluationResultV1 | None = None


@dataclass(frozen=True, slots=True)
class _WorldPlanV1:
    family_id: str
    ordinal: int
    seed: int
    parameter_overrides: dict[str, float]


class SealedCampaignEvaluatorV1:
    """Builds sealed campaigns whose primary world selection never reads strategy behavior."""

    def prepare_campaign(
        self,
        *,
        policy: CampaignPolicyV1,
        generator_bundle: GeneratorBundleV1,
        seed_material: bytes | None = None,
    ) -> PreparedCampaignV1:
        declared = set(policy.same_family_ids) | set(policy.holdout_family_ids)
        for family_id in declared:
            generator_bundle.generator_for(family_id)
        secret_seed_material = seed_material if seed_material is not None else secrets.token_bytes(32)
        if len(secret_seed_material) < 32:
            raise SealedEvaluationError("campaign seed material must contain at least 256 bits")
        public_document = {
            **policy.public_document(),
            "generator_bundle_digest": generator_bundle.digest,
            "secret_seed_commitment": hashlib.sha256(secret_seed_material).hexdigest(),
        }
        commitment = CampaignCommitmentV1(public_document, _digest(public_document))
        return PreparedCampaignV1(commitment, policy, generator_bundle, secret_seed_material)

    def freeze_strategy_artifact(
        self, campaign: PreparedCampaignV1, artifact_bytes: bytes
    ) -> PreparedCampaignV1:
        if campaign.artifact is not None:
            raise SealedEvaluationError("strategy artifact is already frozen")
        if campaign.finalized_primary_result is not None or not artifact_bytes:
            raise SealedEvaluationError("cannot freeze an empty artifact or finalized campaign")
        artifact = FrozenStrategyArtifactV1(hashlib.sha256(artifact_bytes).hexdigest(), len(artifact_bytes))
        return replace(campaign, artifact=artifact)

    def finalize_primary(
        self, campaign: PreparedCampaignV1, *, instruments: tuple[str, ...], steps: int
    ) -> PreparedCampaignV1:
        if campaign.artifact is None:
            raise SealedEvaluationError("strategy artifact must freeze before hidden worlds are generated")
        if campaign.finalized_primary_result is not None:
            raise SealedEvaluationError("primary evaluation is already finalized")
        results: list[PrimaryWorldResultV1] = []
        for plan in self._primary_world_plans(campaign):
            world = campaign.generator_bundle.generator_for(plan.family_id).generate(
                seed=plan.seed,
                instruments=instruments,
                steps=steps,
                parameter_overrides=plan.parameter_overrides,
            )
            observations = self.project_observations(world)
            observation_digest = _digest([asdict(item) for item in observations])
            receipt = hmac.new(
                campaign._secret_seed_material,
                f"world-receipt:{world.digest}".encode(),
                hashlib.sha256,
            ).hexdigest()
            results.append(PrimaryWorldResultV1(receipt, len(observations), observation_digest))
        result = PrimaryEvaluationResultV1(
            campaign.commitment.commitment_digest, campaign.artifact.digest, tuple(results)
        )
        return replace(campaign, finalized_primary_result=result)

    def reveal(self, campaign: PreparedCampaignV1) -> CampaignRevealV1:
        if campaign.finalized_primary_result is None:
            raise SealedEvaluationError("campaign seed material may only be revealed after finalization")
        return CampaignRevealV1(
            campaign.commitment.public_document,
            campaign._secret_seed_material.hex(),
            campaign.policy.reveal_preimage(),
        )

    @staticmethod
    def verify_reveal(commitment: CampaignCommitmentV1, reveal: CampaignRevealV1) -> bool:
        try:
            seed_material = bytes.fromhex(reveal.secret_seed_material_hex)
        except ValueError:
            return False
        if len(seed_material) < 32 or reveal.public_document != commitment.public_document:
            return False
        try:
            allocation = {
                "same_family_ids": sorted(reveal.policy_preimage["same_family_ids"]),
                "holdout_family_ids": sorted(reveal.policy_preimage["holdout_family_ids"]),
            }
            ranges = tuple(
                HiddenParameterRangeV1(**item) for item in reveal.policy_preimage["hidden_parameter_ranges"]
            )
        except (KeyError, TypeError, SealedEvaluationError):
            return False
        return (
            _digest(reveal.public_document) == commitment.commitment_digest
            and reveal.public_document.get("secret_seed_commitment")
            == hashlib.sha256(seed_material).hexdigest()
            and reveal.public_document.get("hidden_family_allocation_commitment") == _digest(allocation)
            and reveal.public_document.get("hidden_parameter_range_commitments")
            == sorted(item.commitment for item in ranges)
        )

    @staticmethod
    def project_observations(world: GeneratedWorldV1) -> tuple[SealedObservationV1, ...]:
        """Project only current exchange-observable fields in deterministic time order."""
        events = sorted(world.events, key=lambda item: item.exchange_time_ns)
        return tuple(
            SealedObservationV1(
                sequence=index,
                exchange_time_ns=event.exchange_time_ns,
                instrument_id=event.instrument_id,
                # Generator-internal event names are a trivial family fingerprint.
                kind="market_update",
                side=event.side,
                price_ticks=event.price_ticks,
                quantity=event.quantity,
            )
            for index, event in enumerate(events)
        )

    def _primary_world_plans(self, campaign: PreparedCampaignV1) -> tuple[_WorldPlanV1, ...]:
        """Derive plans from campaign material only; artifact behavior is never an input."""
        plans: list[_WorldPlanV1] = []
        for family_group, family_ids in (
            ("same_family", campaign.policy.same_family_ids),
            ("family_holdout", campaign.policy.holdout_family_ids),
        ):
            for family_id in family_ids:
                for ordinal in range(campaign.policy.worlds_per_family):
                    context = f"{family_group}:{family_id}:{ordinal}".encode()
                    seed = int.from_bytes(
                        hmac.new(
                            campaign._secret_seed_material, b"world:" + context, hashlib.sha256
                        ).digest()[:8],
                        "big",
                    )
                    overrides: dict[str, float] = {}
                    for parameter_range in campaign.policy.hidden_parameter_ranges:
                        if parameter_range.family_id != family_id:
                            continue
                        sample = hmac.new(
                            campaign._secret_seed_material,
                            b"parameter:" + context + parameter_range.commitment.encode(),
                            hashlib.sha256,
                        ).digest()
                        fraction = int.from_bytes(sample[:8], "big") / 2**64
                        overrides[parameter_range.parameter_name] = parameter_range.lower_bound + fraction * (
                            parameter_range.upper_bound - parameter_range.lower_bound
                        )
                    plans.append(_WorldPlanV1(family_id, ordinal, seed, overrides))
        return tuple(plans)
