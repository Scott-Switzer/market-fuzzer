from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.get("/campaigns/{campaign_id}/report")
def get_report(campaign_id: str) -> dict[str, Any]:
    return {"campaign_id": campaign_id, "status": "not_implemented"}
