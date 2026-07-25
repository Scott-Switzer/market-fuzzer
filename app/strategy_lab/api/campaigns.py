"""Public sealed synthetic campaign API.

All responses are intentionally public-facing only.
Hidden parameters, family IDs, secret seed material, and generator internals
must not appear in API responses.  A reveal path may disclose only commitment
preimages and plan hashes; world-level generator parameters remain private.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.strategy_lab.campaigns.campaign_engine import (
    SealedCampaignDraftV1,
    SealedCampaignEngineError,
    SealedCampaignEngineV1,
    deterministic_world_generation,
)

router = APIRouter()
_engine = SealedCampaignEngineV1()
_campaign_registry: dict[str, dict[str, Any]] = {}


class CampaignCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=20, max_length=2_000)
    same_family_ids: list[str] = Field(min_length=1, max_length=8)
    holdout_family_ids: list[str] = Field(min_length=1, max_length=8)
    worlds_per_family: int = Field(default=1, ge=1, le=32)
    hidden_parameter_ranges: list[dict[str, Any]] | None = Field(default=None, max_length=32)
    scoring_policy_digest: str = Field(min_length=64, max_length=64)
    instruments: list[str] | None = Field(default=None, min_length=1, max_length=32)
    steps: int | None = Field(default=32, ge=1, le=2_000)
    strategy_artifact_digest: str | None = None
    strategy_backend: str = Field(default="deterministic_product_fixture", max_length=120)

    @field_validator("same_family_ids", "holdout_family_ids")
    @classmethod
    def unique_families(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value and value.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("family ids must be unique")
        return cleaned

    @field_validator("scoring_policy_digest")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError("scoring_policy_digest must be a lowercase hex digest")
        return value


class WorldGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family_id: str
    seed: int = Field(ge=0, le=2_147_483_647)
    instruments: list[str] | None = None
    steps: int = Field(default=32, ge=1, le=2_000)
    parameter_overrides: dict[str, Any] | None = None
    heldout_sectors: list[str] | None = None


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _next_campaign_id(payload: CampaignCreateRequest) -> str:
    return f"campaign-{payload.strategy_backend}-{_digest(payload.model_dump(mode='json'))[-10:]}"


def _public_document_for_campaign(campaign_doc: dict[str, Any]) -> dict[str, Any]:
    forbidden = {
        "hidden_parameter_ranges",
        "secret_seed_material_hex",
        "generator_bundle",
        "artifact_bytes",
        "family_ids",
        "holdout_family_ids",
        "same_family_ids",
        "parameter_overrides",
    }
    return {key: value for key, value in campaign_doc.items() if key not in forbidden}


def _public_campaign(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "campaign_id": row["campaign_id"],
        "state": row["state"],
        "public_document": _public_document_for_campaign(row.get("public_document", {})),
        "baseline_result": _public_search_result(row.get("baseline_result")),
        "broad_search_evidence": _public_search_result(row.get("broad_search_evidence")),
        "targeted_search_evidence": _public_search_result(row.get("targeted_search_evidence")),
        "failure_confirmation": _public_failure_confirmation(row.get("failure_confirmation")),
        "evaluation_evidence": row.get("evaluation_evidence"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "world_counts": row.get("world_counts"),
        "strategy_backend": row.get("strategy_backend"),
        "affected_component": "strategy-lab/campaigns/strategy_lab/campaigns/campaign_engine.py",
    }


def _public_search_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result:
        return result
    public: dict[str, Any] = {}
    for key, value in result.items():
        if key == "first_failure_scenario" and value:
            public[key] = {
                "scenario": value.get("scenario"),
                "margin": value.get("margin"),
                "failure": value.get("failure"),
            }
        elif key == "minimized_scenario" and value:
            public[key] = value
        else:
            public[key] = value
    return public


def _public_failure_confirmation(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result:
        return result
    return {
        key: value
        for key, value in result.items()
        if key
        in {
            "stage",
            "confirmed_failure",
            "passing_neighbor",
            "passing_neighbor_scenario_hash",
            "confirmation_budget",
            "failure_rate",
        }
    }


def _search_summary_document(campaign: SealedCampaignDraftV1, stage: str) -> dict[str, Any]:
    source = {
        "baseline": campaign.baseline_result,
        "broad": campaign.broad_search_evidence,
        "targeted": campaign.targeted_search_evidence,
    }[stage] or {}
    document = {
        "campaign_id": campaign.campaign_id,
        "state": campaign.state,
        "stage": stage,
        "evaluated": source.get("evaluated"),
        "failures": source.get("failures"),
    }
    if stage == "targeted":
        document["passing_evaluations"] = source.get("passing_evaluations")
    return {key: value for key, value in document.items() if value is not None}


def _restore_campaign_object(row: dict[str, Any]) -> SealedCampaignDraftV1:
    public_document = row.get("public_document", {})
    try:
        return _engine.prepare_campaign(
            {
                **public_document,
                "name": "restored-campaign",
                "description": "restored sealed campaign",
                "instruments": ["NOVA", "VYNE"],
                "steps": 32,
                "campaign_id": row["campaign_id"],
            }
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"invalid sealed campaign state: {exc}") from exc


def _ensure_campaign(campaign_id: str) -> dict[str, Any]:
    if campaign_id not in _campaign_registry:
        raise HTTPException(404, "sealed campaign not found")
    return _campaign_registry[campaign_id]


@router.post("/campaigns")
def create_campaign(body: CampaignCreateRequest) -> dict[str, Any]:
    campaign_id = _next_campaign_id(body)
    if campaign_id in _campaign_registry:
        raise HTTPException(409, f"sealed campaign {campaign_id} already exists")
    try:
        campaign = _engine.prepare_campaign(
            {
                **body.model_dump(mode="json"),
                "campaign_id": campaign_id,
                "instruments": ["NOVA", "VYNE"],
                "steps": 32,
            }
        )
    except SealedCampaignEngineError as exc:
        raise HTTPException(422, str(exc)) from exc
    _campaign_registry[campaign_id] = {
        "campaign_id": campaign_id,
        "state": campaign.state,
        "public_document": _engine.public_document(campaign),
        "deterministic_world_plans": list(campaign.deterministic_world_plans),
        "baseline_result": None,
        "broad_search_evidence": None,
        "targeted_search_evidence": None,
        "failure_confirmation": None,
        "evaluation_evidence": None,
        "created_at": campaign.created_at,
        "updated_at": campaign.updated_at,
        "world_counts": {
            "same_family": campaign.same_family_world_count,
            "holdout": campaign.holdout_family_world_count,
        },
        "strategy_backend": body.strategy_backend,
    }
    return _public_campaign(_campaign_registry[campaign_id])


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: str) -> dict[str, Any]:
    return _public_campaign(_ensure_campaign(campaign_id))


@router.post("/campaigns/{campaign_id}/worlds/generate")
def generate_world(campaign_id: str, body: WorldGenerationRequest) -> dict[str, Any]:
    row = _ensure_campaign(campaign_id)
    instruments = list(body.instruments or ["ANON-0001"])
    manifest = deterministic_world_generation(
        family_id=body.family_id,
        seed=body.seed,
        instruments=tuple(instruments),
        steps=body.steps,
        parameter_overrides=body.parameter_overrides,
        anonymize=True,
        heldout_sectors=body.heldout_sectors,
    )
    row.setdefault("world_plans", [])
    row["world_plans"].append(
        {
            "group": "on_demand",
            "ordinal": len(row.get("world_plans", [])),
            "seed": body.seed,
            "parameter_overrides_provided": sorted((body.parameter_overrides or {}).keys()),
            "instruments": instruments,
            "steps": body.steps,
            "world_manifest_digest": manifest["digest"],
            "events_digest": manifest["events_digest"],
        }
    )
    return {
        "campaign_id": campaign_id,
        "family_id": body.family_id,
        "world_manifest_id": manifest.get("manifest_id"),
        "anonymized_asset_ids": manifest.get("anonymization_manifest", {}).get(
            "prompt_decoded_tickers", instruments
        ),
        "digest": manifest.get("digest"),
        "events_digest": manifest.get("events_digest"),
        "instrument_count": manifest.get("instrument_count"),
        "entropy_source": manifest.get("entropy_source"),
        "created_at": manifest.get("created_at"),
        "leakage_tests": manifest.get("leakage_tests"),
    }


@router.post("/campaigns/{campaign_id}/run-baseline")
def run_baseline(campaign_id: str) -> dict[str, Any]:
    row = _ensure_campaign(campaign_id)
    try:
        campaign = _engine.run_baseline_search(
            _engine.prepare_campaign(
                {
                    **row.get("public_document", {}),
                    "campaign_id": campaign_id,
                    "name": "restored-campaign",
                    "description": "restored sealed campaign",
                    "instruments": ["NOVA", "VYNE"],
                    "steps": 32,
                }
            )
        )
    except SealedCampaignEngineError as exc:
        raise HTTPException(422, str(exc)) from exc
    row["state"] = campaign.state
    row["updated_at"] = campaign.updated_at
    row["baseline_result"] = campaign.baseline_result
    return _search_summary_document(campaign, "baseline")


@router.post("/campaigns/{campaign_id}/run-broad")
def run_broad(campaign_id: str) -> dict[str, Any]:
    row = _ensure_campaign(campaign_id)
    try:
        campaign = _engine.run_broad_search(_restore_campaign_object(row))
    except SealedCampaignEngineError as exc:
        raise HTTPException(422, str(exc)) from exc
    row["state"] = campaign.state
    row["updated_at"] = campaign.updated_at
    row["broad_search_evidence"] = campaign.broad_search_evidence
    return _search_summary_document(campaign, "broad")


@router.post("/campaigns/{campaign_id}/run-targeted")
def run_targeted(campaign_id: str) -> dict[str, Any]:
    row = _ensure_campaign(campaign_id)
    try:
        campaign = _engine.run_targeted_search(_restore_campaign_object(row))
    except SealedCampaignEngineError as exc:
        raise HTTPException(422, str(exc)) from exc
    row["state"] = campaign.state
    row["updated_at"] = campaign.updated_at
    row["targeted_search_evidence"] = campaign.targeted_search_evidence
    return _search_summary_document(campaign, "targeted")


@router.post("/campaigns/{campaign_id}/confirm-failure")
def confirm_failure(campaign_id: str) -> dict[str, Any]:
    row = _ensure_campaign(campaign_id)
    try:
        campaign = _engine.confirm_failure(_restore_campaign_object(row))
    except SealedCampaignEngineError as exc:
        raise HTTPException(422, str(exc)) from exc
    row["state"] = campaign.state
    row["updated_at"] = campaign.updated_at
    row["failure_confirmation"] = campaign.failure_confirmation
    failure_confirmation = campaign.failure_confirmation or {}
    return {
        "campaign_id": campaign_id,
        "state": campaign.state,
        "confirmed_failure": failure_confirmation.get("confirmed_failure"),
        "failure_rate": failure_confirmation.get("failure_rate"),
        "passing_neighbor": failure_confirmation.get("passing_neighbor"),
        "minimizer": failure_confirmation.get("minimizer"),
        "affected_component": "strategy-lab/campaigns/app/strategy_lab/campaigns/campaign_engine.py",
    }


@router.post("/campaigns/{campaign_id}/deterministic-evaluation")
def deterministic_evaluation(campaign_id: str) -> dict[str, Any]:
    row = _ensure_campaign(campaign_id)
    try:
        campaign = _engine.deterministic_evaluation(_restore_campaign_object(row))
    except SealedCampaignEngineError as exc:
        raise HTTPException(422, str(exc)) from exc
    row["state"] = campaign.state
    row["updated_at"] = campaign.updated_at
    row["evaluation_evidence"] = campaign.evaluation_evidence
    evidence = campaign.evaluation_evidence or {}
    return {
        "campaign_id": campaign_id,
        "state": campaign.state,
        "evaluator_namespace": evidence.get("evaluator_namespace"),
        "world_count": evidence.get("world_count"),
        "world_receipts": evidence.get("world_receipts"),
        "metric_summary": evidence.get("metric_summary"),
        "leakage_guard": evidence.get("leakage_guard"),
    }


@router.post("/campaigns/{campaign_id}/reveal")
def reveal_campaign(campaign_id: str) -> dict[str, Any]:
    row = _ensure_campaign(campaign_id)
    revealed: dict[str, Any] = {
        key: value
        for key, value in {
            "campaign_id": campaign_id,
            "state": row["state"],
            "hidden_parameter_range_commitments": row.get("public_document", {}).get(
                "hidden_parameter_range_commitments"
            ),
            "commitment_digest": row.get("public_document", {}).get("commitment_digest"),
            "strategy_artifact_digest": row.get("public_document", {}).get("strategy_artifact_digest"),
            "world_count": len(row.get("deterministic_world_plans", [])),
        }.items()
        if value not in {None, []}
    }
    plans = row.get("deterministic_world_plans", [])
    if plans:
        world_plan_hashes = []
        for plan in plans:
            world_plan_hashes.append(
                {
                    "group": plan.get("group"),
                    "ordinal": plan.get("ordinal"),
                    "world_manifest_digest": plan.get("world_manifest_digest"),
                    "plan_fingerprint": _digest(
                        {
                            "group": plan.get("group"),
                            "ordinal": plan.get("ordinal"),
                            "parameter_overrides_provided": plan.get("parameter_overrides_provided"),
                            "instruments": plan.get("instruments"),
                            "steps": plan.get("steps"),
                        }
                    ),
                }
            )
        revealed["world_plan_hashes"] = world_plan_hashes
    return revealed


@router.post("/campaigns/{campaign_id}/export")
def export_campaign(campaign_id: str) -> dict[str, Any]:
    try:
        from app.execution_store import ArenaStore
        from app.strategy_lab.evidence.exports import EvidencePackager
        from app.strategy_lab.persistence.repository import StrategyLabRepository
    except Exception as exc:  # pragma: no cover - optional dependency guard
        raise HTTPException(503, f"evidence export dependencies are unavailable: {exc}") from exc
    try:
        _ensure_campaign(campaign_id)
    except HTTPException:
        raise
    try:
        store = ArenaStore()
        repository = StrategyLabRepository(store)
        repository.initialize()
        package = EvidencePackager.build(repository=repository, campaign_id=campaign_id, creator="api")
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"evidence export failed: {exc}") from exc
    return {
        "ok": True,
        "export_id": package["export_id"],
        "campaign_id": package["campaign_id"],
        "manifest": package["manifest"],
        "report_html": package["report_html"],
        "csv_hashes": package["csv_hashes"],
        "affected_component": "strategy-lab/evidence/app/strategy_lab/evidence/exports.py",
    }
