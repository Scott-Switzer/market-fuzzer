from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.strategy_lab.api.campaigns import lookup_public_campaign

router = APIRouter()


@router.get("/campaigns/{campaign_id}/report")
def get_report(campaign_id: str) -> dict[str, Any]:
    campaign = lookup_public_campaign(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {
        "campaign_id": campaign_id,
        "status": "available",
        "state": campaign.get("state"),
        "world_counts": campaign.get("world_counts"),
        "baseline_result": campaign.get("baseline_result"),
        "broad_search_evidence": campaign.get("broad_search_evidence"),
        "targeted_search_evidence": campaign.get("targeted_search_evidence"),
        "failure_confirmation": campaign.get("failure_confirmation"),
        "evaluation_evidence": campaign.get("evaluation_evidence"),
        "claim_boundary": (
            "Synthetic campaign evidence inside a declared configuration. "
            "Not a live-trading, profitability, or production-readiness claim."
        ),
    }
