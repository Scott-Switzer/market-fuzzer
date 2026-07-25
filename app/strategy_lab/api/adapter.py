from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class EvidenceUploadRequest(BaseModel):
    evidence: dict[str, Any]


@router.post("/campaigns/{campaign_id}/evidence")
def upload_evidence(campaign_id: str, body: EvidenceUploadRequest) -> dict[str, Any]:
    # Stub for evidence upload
    return {"campaign_id": campaign_id, "status": "received", "evidence_keys": list(body.evidence.keys())}
