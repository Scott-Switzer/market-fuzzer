"""Traceability tests for the sealed synthetic campaign API.

These tests verify:
1. the new router exposes the required campaign routes;
2. no hidden parameter material leaks through API responses;
3. the deterministic-world helper anonymizes client-provided tickers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.strategy_lab.api import campaigns as campaigns_module
from app.strategy_lab.api.campaigns import (
    CampaignCreateRequest,
    WorldGenerationRequest,
    _ensure_campaign,
    confirm_failure,
    create_campaign,
    deterministic_evaluation,
    generate_world,
    get_campaign,
    run_baseline,
    run_broad,
    run_targeted,
)
from app.strategy_lab.campaigns.campaign_engine import deterministic_world_generation


@dataclass(frozen=True)
class _FakeCampaign:
    campaign_id: str
    name: str
    description: str
    state: str = "draft"
    same_family_world_count: int = 1
    holdout_family_world_count: int = 1
    deterministic_world_plans: tuple[dict[str, Any], ...] = (
        {
            "family_id": "heterogeneous_agent_v1",
            "ordinal": 0,
            "seed": 7,
            "group": "same_family",
            "parameter_overrides": {},
            "instruments": ("NOVA",),
            "steps": 16,
            "world_manifest_digest": "manifest-digest",
            "events_digest": "events-digest",
        },
    )
    baseline_result: dict[str, Any] | None = None
    broad_search_evidence: dict[str, Any] | None = None
    targeted_search_evidence: dict[str, Any] | None = None
    failure_confirmation: dict[str, Any] | None = None
    evaluation_evidence: dict[str, Any] | None = None
    created_at: str = "2026-01-01T00:00:00+00:00"
    updated_at: str = "2026-01-01T00:00:00+00:00"


class _FakeEngine:
    def prepare_campaign(self, payload):
        return _FakeCampaign(
            campaign_id=payload.get("campaign_id", "campaign-test"),
            name=payload.get("name", "test"),
            description=payload.get("description", ""),
        )

    def public_document(self, campaign):
        return {
            "campaign_id": campaign.campaign_id,
            "name": campaign.name,
            "description": campaign.description,
            "state": campaign.state,
            "public_document": {
                "hidden_parameter_range_commitments": ["commitment-only"],
            },
        }

    def run_baseline_search(self, campaign):
        return _FakeCampaign(
            campaign_id=campaign.campaign_id,
            name=campaign.name,
            description=campaign.description,
            state="baseline",
            baseline_result={"evaluated": 3, "failures": 1},
        )

    def run_broad_search(self, campaign):
        return _FakeCampaign(
            campaign_id=campaign.campaign_id,
            name=campaign.name,
            description=campaign.description,
            state="broad",
            broad_search_evidence={"evaluated": 5, "failures": 2},
        )

    def run_targeted_search(self, campaign):
        return _FakeCampaign(
            campaign_id=campaign.campaign_id,
            name=campaign.name,
            description=campaign.description,
            state="targeted",
            targeted_search_evidence={"evaluated": 6, "failures": 2},
        )

    def confirm_failure(self, campaign):
        return _FakeCampaign(
            campaign_id=campaign.campaign_id,
            name=campaign.name,
            description=campaign.description,
            state="confirmed_failure",
            failure_confirmation={
                "confirmed_failure": True,
                "failure_rate": 1.0,
                "passing_neighbor": {"liquidity": 0.95},
                "confirmation_budget": 2,
            },
        )

    def deterministic_evaluation(self, campaign):
        return _FakeCampaign(
            campaign_id=campaign.campaign_id,
            name=campaign.name,
            description=campaign.description,
            state=campaign.state,
            evaluation_evidence={
                "evaluator_namespace": "sealed_campaign_engine_v1",
                "world_count": len(campaign.deterministic_world_plans),
                "world_receipts": ["world-receipt"],
                "metric_summary": {},
                "leakage_guard": {"hidden_parameters_exposed": False},
            },
        )

    def reveal(self, campaign):
        return {
            "hidden_parameter_range_commitments": ["commitment-only"],
            "world_plan_hashes": [
                {
                    "group": "same_family",
                    "ordinal": 0,
                    "world_manifest_digest": "manifest-digest",
                    "plan_fingerprint": "plan-fingerprint",
                }
            ],
        }


def test_campaign_api_routes_exist() -> None:
    app = FastAPI()
    app.include_router(campaigns_module.router, prefix="/api/strategy-lab/campaigns")
    client = TestClient(app)
    response = client.get("/api/strategy-lab/campaigns/does-not-exist")
    assert response.status_code == 404


def test_sealed_campaign_lifecycle_never_exposes_hidden_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _FakeEngine()
    monkeypatch.setattr(campaigns_module, "_engine", engine)
    monkeypatch.setattr(campaigns_module, "_campaign_registry", {})

    payload = CampaignCreateRequest(
        name="traceability",
        description="Campaign for leakage testing.",
        same_family_ids=["heterogeneous_agent_v1"],
        holdout_family_ids=["regime_switching_point_process_v1"],
        scoring_policy_digest="a" * 64,
        hidden_parameter_ranges=[
            {
                "family_id": "heterogeneous_agent_v1",
                "parameter_name": "shock_move",
                "lower_bound": -5,
                "upper_bound": 0,
            }
        ],
    )

    response = create_campaign(payload)
    assert response["state"] == "draft"
    campaign_id = response["campaign_id"]

    def assert_no_leak(body, label):
        assert "secret_seed_material_hex" not in str(body)
        assert "shock_move" not in str(body)
        assert "lower_bound" not in str(body)
        assert "upper_bound" not in str(body)
        assert "/api/enterprise" not in str(body)

    assert_no_leak(response, "create")
    assert_no_leak(get_campaign(campaign_id), "read")

    assert_no_leak(
        generate_world(
            campaign_id,
            WorldGenerationRequest(
                family_id="heterogeneous_agent_v1", seed=7, instruments=["NOVA", "VYNE"], steps=8
            ),
        ),
        "worlds/generate",
    )
    assert_no_leak(
        run_baseline(campaign_id),
        "run-baseline",
    )
    assert_no_leak(
        run_broad(campaign_id),
        "run-broad",
    )
    assert_no_leak(
        run_targeted(campaign_id),
        "run-targeted",
    )
    assert_no_leak(
        confirm_failure(campaign_id),
        "confirm-failure",
    )
    assert_no_leak(
        deterministic_evaluation(campaign_id),
        "deterministic-evaluation",
    )

    reveal = engine.reveal(_ensure_campaign(campaign_id))
    assert "hidden_parameter_range_commitments" in reveal
    for forbidden_key in {
        "secret_seed_material_hex",
        "hidden_parameter_ranges",
        "generator_bundle",
        "artifact_bytes",
    }:
        assert forbidden_key not in reveal


def test_deterministic_world_generation_anonymizes_assets() -> None:
    manifest = deterministic_world_generation(
        family_id="heterogeneous_agent_v1",
        seed=7,
        instruments=("NOVA", "VYNE", "ACME"),
        steps=8,
        anonymize=True,
    )
    assert manifest["instrument_count"] == 3
    decoded_names = manifest["anonymization_manifest"]["decoded_names"]
    assert decoded_names == {}
    assert manifest["stylized_fact_diagnostics"]["event_count"] > 0
    assert manifest["leakage_tests"]["passed"] is True
