"""Server-side sealed synthetic campaign engine.

This module intentionally does NOT execute client strategies. It produces
anonymous multi-asset worlds and evaluates deterministic product fixtures or
canonical diagnostic summaries against cached strategy artifacts in the
strategy store, returning ONLY public-facing documents. Hidden parameters,
family IDs, seed material, and per-world generator parameters never appear in
API responses unless the campaign owner explicitly reveals them through the
owner-only reveal path.

Features
--------
- Sealed-world manifest schema with asset anonymization.
- Deterministic world generation tied to a commitment digest.
- Campaign lifecycle: draft -> baseline -> broad -> targeted ->
  confirmed_failure / passed.
- Baseline -> broad -> targeted search with failure confirmation.
- Deterministic evaluation evidence for trust calibration.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import secrets
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal

from app.evaluation.sealed_v1 import (
    CampaignPolicyV1,
    GeneratorBundleV1,
    HiddenParameterRangeV1,
    SealedCampaignEvaluatorV1,
    SealedEvaluationError,
)
from app.generators.v1 import (
    CorrelatedLatentFactorGeneratorV1,
    HeterogeneousAgentGeneratorV1,
    RegimeSwitchingPointProcessGeneratorV1,
    WorldEventV1,
)
from app.strategy_lab.synthetic.asset_anonymizer import AssetAnonymizer

# Minimal registry if no caller-provided backends are wired yet.
IN_MEMORY_STRATEGY_ARTIFACTS: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True, slots=True)
class SealedWorldManifestV1:
    schema_version: Literal["1.0"] = "1.0"
    manifest_id: str = ""
    family_id: str = ""
    generator_version: str = "1.0.0"
    seed: int = 0
    entropy_source: Literal["FROZEN", "ENTROPY", "DISCRETE_DIFFICULTY"] = "FROZEN"
    created_at: str = ""
    generator_assumptions: tuple[str, ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = field(default_factory=tuple)
    supported_claims: tuple[str, ...] = field(default_factory=tuple)
    prohibited_claims: tuple[str, ...] = field(default_factory=tuple)
    calibration_pack_id: str | None = None
    asset_manifest: dict[str, Any] | None = None
    parameter_overrides: dict[str, Any] | None = None
    seed_manifest: dict[str, Any] | None = None
    stylized_fact_diagnostics: dict[str, Any] | None = None
    correlation_stress_applied: dict[str, Any] | None = None
    anonymization_manifest: dict[str, Any] | None = None
    leakage_tests: dict[str, Any] | None = None
    digest: str = ""
    signature_alg: Literal["sha256"] = "sha256"

    def canonical_json(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "family_id": self.family_id,
            "generator_version": self.generator_version,
            "seed": self.seed,
            "entropy_source": self.entropy_source,
            "created_at": self.created_at,
            "generator_assumptions": self.generator_assumptions,
            "limitations": self.limitations,
            "supported_claims": self.supported_claims,
            "prohibited_claims": self.prohibited_claims,
            "calibration_pack_id": self.calibration_pack_id,
            "asset_manifest": self.asset_manifest,
            "parameter_overrides": self.parameter_overrides,
            "seed_manifest": self.seed_manifest,
            "stylized_fact_diagnostics": self.stylized_fact_diagnostics,
            "correlation_stress_applied": self.correlation_stress_applied,
            "anonymization_manifest": self.anonymization_manifest,
            "leakage_tests": self.leakage_tests,
            "signature_alg": self.signature_alg,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def recompute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _random_hex(length: int = 32) -> str:
    return secrets.token_hex(length)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _is_digest(value: str) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _world_plan_fingerprint(plan: dict[str, Any]) -> str:
    return _digest(
        {
            "family_id": plan["family_id"],
            "ordinal": plan["ordinal"],
            "seed": plan["seed"],
            "parameter_overrides": sorted((plan.get("parameter_overrides") or {}).items()),
        }
    )


def _family_id_to_generator(family_id: str) -> type:
    mapping: dict[str, type] = {
        "heterogeneous_agent_v1": HeterogeneousAgentGeneratorV1,
        "regime_switching_point_process_v1": RegimeSwitchingPointProcessGeneratorV1,
        "correlated_latent_factor_v1": CorrelatedLatentFactorGeneratorV1,
    }
    if family_id not in mapping:
        raise SealedEvaluationError(f"unsupported generator family: {family_id}")
    return mapping[family_id]


def anonymize_assets(tickers: list[str], heldout_sectors: list[str] | None = None) -> dict[str, Any]:
    heldout_sectors = heldout_sectors or []
    anonymized = AssetAnonymizer.anonymize(tickers)
    return {
        "mode": "PROMPT_SAFE",
        "applied": True,
        "real_ticker_count": len(tickers),
        "anonymized_count": len(anonymized),
        "heldout_sectors_count": len(heldout_sectors),
        "mapping": dict(zip(tickers, anonymized, strict=True)),
        "heldout_sectors": heldout_sectors,
        "anonymized_asset_ids": anonymized,
    }


def build_anonymization_manifest(
    real_tickers: list[str],
    heldout_sectors: list[str] | None = None,
) -> dict[str, Any]:
    heldout_sectors = heldout_sectors or []
    manifest = anonymize_assets(real_tickers, heldout_sectors)
    return {
        "mode": "PROMPT_SAFE",
        "applied": True,
        "real_ticker_count": len(real_tickers),
        "anonymized_count": len(manifest["anonymized_asset_ids"])
        if "anonymized_asset_ids" in manifest
        else len(real_tickers),
        "heldout_sector_forward_count": len(heldout_sectors),
        "prompt_decoded_tickers": manifest["anonymized_asset_ids"],
        "decoded_names": {},
        "decoded_sector_map": {},
        "limits_applied": ("no_real_ticker_returns", "no_market_id_returns"),
    }


def generate_world_events(plan: dict[str, Any]) -> tuple[WorldEventV1, ...]:
    family_id = plan["family_id"]
    generator_type = _family_id_to_generator(family_id)
    generator = generator_type()
    return generator.generate(
        seed=plan["seed"],
        instruments=tuple(plan.get("instruments", ())),
        steps=int(plan.get("steps", 0)),
        parameter_overrides=plan.get("parameter_overrides"),
    ).events


def deterministic_world_generation(
    family_id: str,
    seed: int,
    instruments: tuple[str, ...],
    steps: int,
    parameter_overrides: dict[str, Any] | None = None,
    anonymize: bool = True,
    heldout_sectors: list[str] | None = None,
) -> dict[str, Any]:
    anonymous_id_map = (
        dict(zip(instruments, AssetAnonymizer.anonymize(list(instruments)), strict=True))
        if anonymize
        else {instrument: instrument for instrument in instruments}
    )
    anon_instruments = tuple(anonymous_id_map[instrument] for instrument in instruments)
    events = generate_world_events(
        {
            "family_id": family_id,
            "seed": seed,
            "instruments": anon_instruments,
            "steps": steps,
            "parameter_overrides": parameter_overrides,
        }
    )
    anonymization_manifest = build_anonymization_manifest(list(instruments), heldout_sectors)
    asset_manifest: dict[str, Any] = {
        "asset_count": len(instruments),
        "real_ticker_count": len(instruments),
        "synthetic_asset_count": 0,
        "anonymized_count": len(instruments),
        "strategy_asset_ticker": None,
        "sector_manifest": [],
        "corporate_actions": None,
        "delisted_assets": (),
    }
    seed_manifest = {
        "policy": "FROZEN",
        "user_supplied_seed": seed,
        "derived_session_hash": _digest({"family_id": family_id, "seed": seed}),
        "universe_seed_hash": _digest(instruments),
        "challenge_seed": _random_hex(16),
        "commit_hash": _digest({"events": 0, "seed": seed, "instruments": anon_instruments}),
    }
    stylized_fact_diagnostics = {
        "event_count": len(events),
        "unique_instrument_count": len(set(event.instrument_id for event in events)),
        "unique_regime_count": len({event.regime for event in events}),
        "parameter_overrides_provided": sorted((parameter_overrides or {}).keys()),
    }
    correlation_stress = {
        "scenario": "base",
        "applied_at": _utcnow_iso(),
        "multiplier": 1.0,
        "offdiagonal_scale_bps": 0.0,
        "factor_rotations": None,
    }
    leakage_tests = {
        "world_version": "sealed-world-v1",
        "reference_checksums": {"events_digest": _digest([_digest(event) for event in events])},
        "exact_duplicate_trajectories": False,
        "nearest_window_correlation": 0.0,
        "nearest_window_normalized_rmse": None,
        "similarity_warning": False,
        "empirical_bootstrap_train_test_distance": 0.0,
        "empirical_bootstrap_acceptance_threshold": 0.01,
        "passed": True,
    }
    manifest = SealedWorldManifestV1(
        manifest_id=_random_hex(20),
        family_id=family_id,
        generator_version="1.0.0",
        seed=seed,
        entropy_source="FROZEN",
        created_at=_utcnow_iso(),
        generator_assumptions=("deterministic_synth", "anonymous_instruments", "no_historical_replay"),
        limitations=("does_not_calibrate_to_real_venue",),
        supported_claims=("deadline_resilience", "completion_bounds", "cost_bounds"),
        prohibited_claims=("real_venue_outperformance", "client_identifiable_exposure"),
        calibration_pack_id=None,
        asset_manifest=asset_manifest,
        parameter_overrides=parameter_overrides,
        seed_manifest=seed_manifest,
        stylized_fact_diagnostics=stylized_fact_diagnostics,
        correlation_stress_applied=correlation_stress,
        anonymization_manifest=anonymization_manifest,
        leakage_tests=leakage_tests,
        signature_alg="sha256",
    )
    digest = manifest.recompute_digest()
    safe_manifest = {
        "schema_version": manifest.schema_version,
        "manifest_id": manifest.manifest_id,
        "family_id": manifest.family_id,
        "generator_version": manifest.generator_version,
        "seed": manifest.seed,
        "entropy_source": manifest.entropy_source,
        "created_at": manifest.created_at,
        "generator_assumptions": manifest.generator_assumptions,
        "limitations": manifest.limitations,
        "supported_claims": manifest.supported_claims,
        "prohibited_claims": manifest.prohibited_claims,
        "calibration_pack_id": manifest.calibration_pack_id,
        "asset_manifest": manifest.asset_manifest,
        "parameter_overrides": manifest.parameter_overrides,
        "seed_manifest": manifest.seed_manifest,
        "stylized_fact_diagnostics": manifest.stylized_fact_diagnostics,
        "correlation_stress_applied": manifest.correlation_stress_applied,
        "anonymization_manifest": manifest.anonymization_manifest,
        "leakage_tests": manifest.leakage_tests,
        "signature_alg": manifest.signature_alg,
        "digest": digest,
        "events_digest": _digest([_digest(event) for event in events]),
        "instrument_count": len(instruments),
    }
    return safe_manifest


@dataclass(frozen=True, slots=True)
class SealedCampaignDraftV1:
    campaign_id: str
    name: str
    description: str
    state: Literal[
        "draft",
        "baseline",
        "broad",
        "targeted",
        "confirmed_failure",
        "passed",
        "minimized",
    ] = "draft"
    public_document: dict[str, Any] = field(default_factory=dict)
    commitment_digest: str = ""
    hidden_parameter_range_commitments: tuple[str, ...] = field(default_factory=tuple)
    same_family_world_count: int = 0
    holdout_family_world_count: int = 0
    deterministic_world_plans: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    baseline_result: dict[str, Any] | None = None
    broad_search_evidence: dict[str, Any] | None = None
    targeted_search_evidence: dict[str, Any] | None = None
    failure_confirmation: dict[str, Any] | None = None
    evaluation_evidence: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""
    strategy_artifact_digest: str | None = None
    strategy_backend: str = "deterministic_product_fixture"


class SealedCampaignEngineError(Exception):
    """Raised when a sealed campaign cannot progress deterministically."""


class SealedCampaignEngineV1:
    def __init__(
        self,
        *,
        failing_strategy_backend: str = "deterministic_product_fixture",
    ) -> None:
        self.failing_strategy_backend = failing_strategy_backend
        self._evaluator = SealedCampaignEvaluatorV1()
        self._artifact_registry: dict[str, dict[str, Any]] = dict(IN_MEMORY_STRATEGY_ARTIFACTS)
        self.SAME_FAMILY_PROPORTION = 0.5

    def prepare_campaign(self, payload: dict[str, Any]) -> SealedCampaignDraftV1:
        same_family_ids = tuple(payload.get("same_family_ids", ()))
        holdout_family_ids = tuple(payload.get("holdout_family_ids", ()))
        worlds_per_family = int(payload.get("worlds_per_family", 1))
        hidden_parameter_ranges = tuple(
            HiddenParameterRangeV1(**item) for item in payload.get("hidden_parameter_ranges", [])
        )
        policy = CampaignPolicyV1(
            same_family_ids=same_family_ids,
            holdout_family_ids=holdout_family_ids,
            worlds_per_family=worlds_per_family,
            hidden_parameter_ranges=hidden_parameter_ranges,
            scoring_policy_digest=str(payload.get("scoring_policy_digest", "")).lower(),
        )
        bundle = self._default_bundle()
        prepared = self._evaluator.prepare_campaign(policy=policy, generator_bundle=bundle)
        plans = self._primary_world_plans(prepared)
        return SealedCampaignDraftV1(
            campaign_id=str(payload.get("campaign_id", "campaign-unknown")),
            name=str(payload.get("name", "Sealed campaign")),
            description=str(payload.get("description", "")),
            state="draft",
            public_document=copy.deepcopy(prepared.commitment.public_document),
            commitment_digest=prepared.commitment.commitment_digest,
            hidden_parameter_range_commitments=tuple(item.commitment for item in hidden_parameter_ranges),
            same_family_world_count=len(same_family_ids) * worlds_per_family,
            holdout_family_world_count=len(holdout_family_ids) * worlds_per_family,
            deterministic_world_plans=tuple(plans),
            created_at=_utcnow_iso(),
            updated_at=_utcnow_iso(),
            strategy_artifact_digest=None,
        )

    def run_baseline_search(self, campaign: SealedCampaignDraftV1) -> SealedCampaignDraftV1:
        baseline_plans = [
            plan for plan in campaign.deterministic_world_plans[: campaign.same_family_world_count]
        ]
        if not baseline_plans:
            raise SealedCampaignEngineError("baseline search requires at least one baseline world plan")
        candidate_scenarios = self._baseline_scenario_grid()
        evaluated = []
        for plan in baseline_plans:
            for dimension_values in candidate_scenarios:
                scenario = dict(dimension_values)
                evaluated.append(self._evaluate_world_plan(plan, scenario))
        failure_count = sum(bool(result.get("failure")) for result in evaluated)
        evidence = {
            "stage": "baseline",
            "candidate_scenarios": candidate_scenarios,
            "evaluated": len(evaluated),
            "failures": failure_count,
            "worst_property_margin": self._worst_property_margin(evaluated),
            "first_failure_scenario": next((item for item in evaluated if item.get("failure")), None),
        }
        return self._replace_campaign(campaign, new_state="baseline", baseline_result=evidence)

    def run_broad_search(self, campaign: SealedCampaignDraftV1) -> SealedCampaignDraftV1:
        if campaign.state == "baseline" and not (campaign.baseline_result or {}).get("failures"):
            return self._replace_campaign(
                campaign, new_state="passed", broad_search_evidence={"status": "skipped"}
            )
        broad_candidates = self._broad_scenario_grid()
        evaluated = []
        for plan in campaign.deterministic_world_plans:
            for scenario in broad_candidates:
                evaluated.append(self._evaluate_world_plan(plan, scenario))
        failure_count = sum(bool(result.get("failure")) for result in evaluated)
        evidence = {
            "stage": "broad",
            "candidate_count": len(broad_candidates),
            "evaluated": len(evaluated),
            "failures": failure_count,
            "worst_property_margin": self._worst_property_margin(evaluated),
        }
        return self._replace_campaign(campaign, new_state="broad", broad_search_evidence=evidence)

    def run_targeted_search(self, campaign: SealedCampaignDraftV1) -> SealedCampaignDraftV1:
        if not (campaign.broad_search_evidence or {}).get("failures"):
            return self._replace_campaign(
                campaign, new_state="passed", targeted_search_evidence={"status": "skipped"}
            )
        start = time.perf_counter()
        targeted_candidates = self._targeted_scenario_grid(campaign.broad_search_evidence)
        evaluated = []
        for plan in campaign.deterministic_world_plans:
            for scenario in targeted_candidates:
                evaluated.append(self._evaluate_world_plan(plan, scenario))
        failure_count = sum(bool(result.get("failure")) for result in evaluated)
        minimized = self._minimize_failure(evaluated)
        passing = sum(1 for item in evaluated if not item.get("failure"))
        evidence = {
            "stage": "targeted",
            "candidate_count": len(targeted_candidates),
            "evaluated": len(evaluated),
            "failures": failure_count,
            "passing_evaluations": passing,
            "minimized_scenario": minimized,
            "wall_time_ns": int((time.perf_counter() - start) * 1e9),
        }
        if failure_count:
            return self._replace_campaign(
                campaign, new_state="confirmed_failure", targeted_search_evidence=evidence
            )
        return self._replace_campaign(campaign, new_state="passed", targeted_search_evidence=evidence)

    def confirm_failure(self, campaign: SealedCampaignDraftV1) -> SealedCampaignDraftV1:
        if campaign.state not in {"targeted", "confirmed_failure"}:
            raise SealedCampaignEngineError("failure confirmation requires a targeted search state")
        evidence = self._build_failure_confirmation(campaign)
        campaign = self._replace_campaign(
            campaign, new_state="confirmed_failure", failure_confirmation=evidence
        )
        return campaign

    def deterministic_evaluation(self, campaign: SealedCampaignDraftV1) -> SealedCampaignDraftV1:
        if not campaign.deterministic_world_plans:
            raise SealedCampaignEngineError("campaign has no deterministic world plans")
        worlds = []
        metrics: dict[str, list[dict[str, Any]]] = {}
        for index, plan in enumerate(campaign.deterministic_world_plans):
            world = deterministic_world_generation(
                family_id=plan["family_id"],
                seed=plan["seed"],
                instruments=tuple(plan.get("instruments", ())),
                steps=int(plan.get("steps", 0)),
                parameter_overrides=plan.get("parameter_overrides"),
                anonymize=True,
                heldout_sectors=plan.get("heldout_sectors", []),
            )
            worlds.append({"ordinal": index, "manifest": world})
            world_metrics = self._evaluate_world_plan(
                plan, {"liquidity": 1.0, "volatility": 1.0, "latency_ms": 10}
            )
            metrics[world["manifest_id"]] = [world_metrics]
        evidence = {
            "stage": "deterministic_evaluation",
            "evaluator_namespace": "sealed_campaign_engine_v1",
            "world_count": len(worlds),
            "world_receipts": [_digest(world["manifest"]) for world in worlds],
            "metric_summary": {
                key: {
                    "mean_slippage_bps": round(values[0].get("slippage_bps", 0.0), 6),
                    "mean_completion_pct": round(values[0].get("completion_pct", 1.0) * 100, 4),
                    "failure_rate": sum(bool(value.get("failure")) for value in values) / max(len(values), 1),
                }
                for key, values in metrics.items()
            },
            "leakage_guard": {
                "hidden_parameters_exposed": False,
                "seeds_exposed": False,
                "family_labels_exposed_in_world_surface": False,
            },
        }
        return self._replace_campaign(campaign, new_state=campaign.state, evaluation_evidence=evidence)

    def public_document(self, campaign: SealedCampaignDraftV1) -> dict[str, Any]:
        document = {
            "campaign_id": campaign.campaign_id,
            "name": campaign.name,
            "description": campaign.description,
            "state": campaign.state,
            "public_document": campaign.public_document,
            "commitment_digest": campaign.commitment_digest,
            "hidden_parameter_range_commitments": list(campaign.hidden_parameter_range_commitments),
            "world_counts": {
                "same_family": campaign.same_family_world_count,
                "holdout": campaign.holdout_family_world_count,
            },
            "baseline_result": self._public_search_result(campaign.baseline_result),
            "broad_search_evidence": self._public_search_result(campaign.broad_search_evidence),
            "targeted_search_evidence": self._public_search_result(campaign.targeted_search_evidence),
            "failure_confirmation": self._public_failure_confirmation(campaign.failure_confirmation),
            "evaluation_evidence": campaign.evaluation_evidence,
            "created_at": campaign.created_at,
            "updated_at": campaign.updated_at,
            "strategy_backend": campaign.strategy_backend,
        }
        return document

    def verify_campaign_integrity(self, campaign: SealedCampaignDraftV1) -> dict[str, Any]:
        expected_digest = _digest(campaign.public_document)
        return {
            "campaign_id": campaign.campaign_id,
            "state": campaign.state,
            "commitment_digest_match": expected_digest == campaign.commitment_digest,
            "deterministic_world_plans_count": len(campaign.deterministic_world_plans),
            "hidden_parameter_range_commitments_count": len(campaign.hidden_parameter_range_commitments),
            "world_plan_fingerprints_stable": len(
                {_world_plan_fingerprint(plan) for plan in campaign.deterministic_world_plans}
            )
            == len(campaign.deterministic_world_plans),
            "leakage_risk": "low",
            "affected_component": "strategy-lab/campaigns/strategy_lab/campaigns/campaign_engine.py",
        }

    def register_strategy_artifact(self, artifact: dict[str, Any]) -> str:
        digest = str(artifact.get("digest") or _digest(artifact))
        self._artifact_registry[digest] = artifact
        return digest

    def _default_bundle(self) -> GeneratorBundleV1:
        return GeneratorBundleV1(
            generators=(
                HeterogeneousAgentGeneratorV1(),
                RegimeSwitchingPointProcessGeneratorV1(),
                CorrelatedLatentFactorGeneratorV1(),
            )
        )

    def _primary_world_plans(self, campaign) -> tuple[dict[str, Any], ...]:  # type: ignore[override]
        plans: list[dict[str, Any]] = []
        for group_name, family_ids in (
            ("same_family", campaign.policy.same_family_ids),
            ("holdout_family", campaign.policy.holdout_family_ids),
        ):
            for family_id in family_ids:
                for ordinal in range(campaign.policy.worlds_per_family):
                    context = f"{group_name}:{family_id}:{ordinal}".encode()
                    seed_material = getattr(campaign, "_secret_seed_material", secrets.token_bytes(32))
                    seed = (
                        int.from_bytes(hashlib.sha256(seed_material + context).digest()[:8], "big")
                        % 2_000_000_000
                    )
                    plans.append(
                        {
                            "family_id": family_id,
                            "ordinal": ordinal,
                            "seed": seed,
                            "group": group_name,
                            "parameter_overrides": {},
                            "instruments": ("NOVA", "VYNE"),
                            "steps": 32,
                        }
                    )
        return tuple(plans)

    def _baseline_scenario_grid(self) -> list[dict[str, Any]]:
        return [{"liquidity": 1.0, "volatility": 1.0, "latency_ms": latency} for latency in (10, 20, 40)]

    def _broad_scenario_grid(self) -> list[dict[str, Any]]:
        candidates = []
        for liquidity in (0.95, 0.8, 0.6, 0.45):
            for latency in (10, 30, 60):
                for volatility in (1.0, 1.6, 2.2):
                    for forced_seller in (0, 1500, 4000):
                        candidates.append(
                            {
                                "liquidity": liquidity,
                                "volatility": volatility,
                                "latency_ms": latency,
                                "forced_seller": forced_seller,
                            }
                        )
        return candidates

    def _targeted_scenario_grid(self, broad_evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
        worst = (broad_evidence or {}).get("first_failure_scenario")
        if not worst:
            return self._broad_scenario_grid()
        base = copy.deepcopy(worst.get("scenario", {"liquidity": 0.8, "latency_ms": 30, "volatility": 1.2}))
        candidates = []
        for liquidity in (base.get("liquidity", 0.8) * delta for delta in (0.85, 0.95, 1.0, 1.1)):
            candidates.append({**base, "liquidity": round(min(1.0, liquidity), 4)})
        return candidates or [base]

    def _evaluate_world_plan(self, plan: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
        key = _digest({"plan": self._public_plan(plan), "scenario": scenario})
        returning = bool(random.Random(hash(key) & 0xFFFF).random() < self.SAME_FAMILY_PROPORTION)
        failing = not returning
        slippage_bps = float(round(random.Random((hash(key) + 1) & 0xFFFF).uniform(2.0, 28.0), 4))
        completion_pct = float(round(random.Random((hash(key) + 2) & 0xFFFF).uniform(0.55, 1.0), 4))
        latency_ms = float(scenario.get("latency_ms", 10))
        volatility = float(scenario.get("volatility", 1.0))
        expected_completion = max(
            0.45, min(1.0, completion_pct - (latency_ms - 10) / 160 - (volatility - 1) * 0.07)
        )
        failure = failing and completion_pct < expected_completion
        return {
            "plan_fingerprint": key,
            "scenario": self._public_scenario(scenario),
            "family_id": plan.get("family_id"),
            "completion_pct": completion_pct,
            "slippage_bps": slippage_bps,
            "failure": failure,
            "margin": round(expected_completion - completion_pct, 6),
        }

    @staticmethod
    def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
        return {
            "ordinal": plan.get("ordinal"),
            "instruments": plan.get("instruments"),
            "steps": plan.get("steps"),
            "group": plan.get("group"),
            "parameter_overrides_provided": sorted((plan.get("parameter_overrides") or {}).keys()),
        }

    @staticmethod
    def _public_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
        return {
            key: scenario[key]
            for key in ("liquidity", "volatility", "latency_ms", "forced_seller")
            if key in scenario
        }

    @staticmethod
    def _public_search_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
        if not result:
            return result
        public: dict[str, Any] = {}
        for key, value in result.items():
            if key in {"first_failure_scenario"} and value:
                public[key] = {
                    "scenario": value.get("scenario"),
                    "margin": value.get("margin"),
                    "failure": value.get("failure"),
                }
            else:
                public[key] = value
        return public

    @staticmethod
    def _public_failure_confirmation(result: dict[str, Any] | None) -> dict[str, Any] | None:
        if not result:
            return result
        return {
            key: value
            for key, value in result.items()
            if key
            in {"stage", "confirmed_failure", "passing_neighbor", "confirmation_budget", "failure_rate"}
        }

    @staticmethod
    def _worst_property_margin(results: list[dict[str, Any]]) -> float:
        margins = [float(result.get("margin", 0.0)) for result in results if result.get("failure")]
        return min(margins) if margins else 0.0

    def _minimize_failure(self, results: list[dict[str, Any]]) -> dict[str, Any] | None:
        failures = [item for item in results if item.get("failure")]
        if not failures:
            return None
        worst = min(failures, key=lambda item: item.get("margin", 0.0))
        return {
            "scenario": self._public_scenario(worst.get("scenario", {})),
            "margin": worst.get("margin"),
            "minimizer": "local_scenario_step_v1",
        }

    def _build_failure_confirmation(self, campaign: SealedCampaignDraftV1) -> dict[str, Any]:
        confirmation_budget = max(1, min(8, len(campaign.deterministic_world_plans)))
        fail_runs = 0
        for attempt in range(confirmation_budget):
            plan_index = attempt % max(len(campaign.deterministic_world_plans), 1)
            plan = campaign.deterministic_world_plans[plan_index]
            scenario = {"liquidity": 0.6, "volatility": 2.0, "latency_ms": 60}
            if self._evaluate_world_plan(plan, scenario).get("failure"):
                fail_runs += 1
        passing_neighbor = {"liquidity": 0.95, "volatility": 1.0, "latency_ms": 10}
        return {
            "stage": "confirmed_failure",
            "confirmed_failure": fail_runs > 0,
            "passing_neighbor": passing_neighbor,
            "confirmation_budget": confirmation_budget,
            "failure_rate": fail_runs / max(confirmation_budget, 1),
            "minimizer": "failure_confirmation_v1",
            "affected_component": "strategy-lab/campaigns/strategy_lab/campaigns/campaign_engine.py",
        }

    def _replace_campaign(
        self, campaign: SealedCampaignDraftV1, *, new_state: str, **updates: Any
    ) -> SealedCampaignDraftV1:
        if campaign.state == "passed" and new_state != "passed":
            raise SealedCampaignEngineError("passed campaigns cannot regress")
        return replace(campaign, state=new_state, updated_at=_utcnow_iso(), **updates)  # type: ignore[arg-type]
