from __future__ import annotations

from typing import Any

from app.strategy_lab.robustness.failure_taxonomy import (
    ThresholdPredicate,
)
from app.strategy_lab.robustness.minimizer import minimize as delta_debug_minimize
from app.strategy_lab.robustness.replay import build_artifact, store_artifact
from app.strategy_lab.robustness.search import search as adversarial_search
from app.strategy_lab.robustness.suggestions import EvidenceLinkedSuggestionEngine


def run_failure_campaign(
    *,
    campaign_id: str,
    strategy_type: str,
    params: dict[str, Any],
    search_space: dict[str, tuple[float, float]],
    predicates: list[ThresholdPredicate],
    budget: int = 64,
    seed: int = 42,
    method: str = "sobol",
) -> dict[str, Any]:
    search_result = adversarial_search(
        strategy_type=strategy_type,
        params=params,
        search_space=search_space,
        predicates=predicates,
        budget=budget,
        seed=seed,
        method=method,
    )
    replay_root = "artifacts/replays"
    recorded: list[dict[str, Any]] = []
    for idx, failure in enumerate(search_result.get("failures", [])):
        minimized = delta_debug_minimize(failure, max_iterations=16, seed=seed + idx)
        suggestions = EvidenceLinkedSuggestionEngine.suggest(minimized.get("minimized_failure", failure))
        artifact = build_artifact(
            campaign_id=campaign_id,
            evaluation_index=failure.get("evaluation_index", idx),
            strategy_type=strategy_type,
            parameters=failure.get("parameters", params),
            world=failure.get("world_spec", {}),
            prices=[],
            positions=[],
            metrics=failure.get("metrics", {}),
            failure_record={**failure, "suggestions": suggestions},
        )
        store = store_artifact(replay_root, artifact)
        recorded.append(
            {
                "evaluation_index": failure.get("evaluation_index", idx),
                "category": failure.get("category"),
                "severity": failure.get("severity", "high"),
                "failed_predicates": failure.get("failed_predicates", []),
                "metrics": failure.get("metrics", {}),
                "method": failure.get("method", method),
                "replay_artifact_id": artifact.artifact_id,
                "replay_path": store.get("path"),
                "minimized": minimized.get("minimized_failure"),
                "suggestions": suggestions,
            }
        )
    return {
        "campaign_id": campaign_id,
        "status": "completed",
        "method": method,
        "budget": budget,
        "evaluated": search_result.get("evaluated", 0),
        "failures": recorded,
        "failure_count": len(recorded),
    }
