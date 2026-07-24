from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.strategy_lab.robustness.failure_taxonomy import ThresholdPredicate
from app.strategy_lab.robustness.orchestrator import run_failure_campaign
from app.strategy_lab.robustness.replay import load_artifact

router = APIRouter()


def _get_repository() -> Any:
    from app.execution_store import ArenaStore
    from app.strategy_lab.persistence.repository import StrategyLabRepository

    return StrategyLabRepository(ArenaStore())


@router.post("/campaigns/{campaign_id}/failures")
def trigger_failure_campaign(campaign_id: str, body: dict[str, Any]) -> dict[str, Any]:
    if not body:
        raise HTTPException(status_code=400, detail="Empty campaign payload")
    predicates_raw = body.get("predicates", [])
    predicates = [ThresholdPredicate(**item) for item in predicates_raw]
    result = run_failure_campaign(
        campaign_id=campaign_id,
        strategy_type=body.get("strategy_type", "sma_crossover"),
        params=body.get("params", {"fast": 20, "slow": 50}),
        search_space=body.get("search_space", {}),
        predicates=predicates,
        budget=int(body.get("budget", 64)),
        seed=int(body.get("seed", 42)),
        method=body.get("method", "sobol"),
    )
    _get_repository().save(f"campaign:{campaign_id}:failures", result)
    return result


@router.get("/campaigns/{campaign_id}/failures")
def list_failures(campaign_id: str) -> dict[str, Any]:
    stored = _get_repository().load(f"campaign:{campaign_id}:failures")
    if stored is None:
        return {"campaign_id": campaign_id, "status": "not_found", "failures": []}
    return stored


@router.get("/replay/{artifact_id}")
def get_replay(artifact_id: str) -> dict[str, Any]:
    artifact = load_artifact(None, artifact_id)
    if artifact.get("status") == "missing":
        raise HTTPException(status_code=404, detail="Replay artifact not found")
    return artifact


@router.get("/report/{campaign_id}")
def campaign_report(campaign_id: str) -> dict[str, Any]:
    stored = _get_repository().load(f"campaign:{campaign_id}:failures")
    if stored is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {
        "campaign_id": campaign_id,
        "summary": {
            "method": stored.get("method"),
            "budget": stored.get("budget"),
            "evaluated": stored.get("evaluated"),
            "failure_count": stored.get("failure_count"),
        },
        "evidence": stored.get("failures", []),
        "status": "completed",
    }
