from __future__ import annotations

import hashlib
from typing import Any

from app.strategy_lab.robustness.failure_taxonomy import (
    FailureCategory,
    FailureEvidence,
    FailureRecord,
    FailureSeverity,
    ThresholdPredicate,
    build_predicates,
    evaluate_predicates,
)


def _failure_key(failure: dict[str, Any]) -> str:
    return hashlib.sha256(str(sorted(failure.items())).encode("utf-8")).hexdigest()[:12]


def _world_params(world: dict[str, Any]) -> dict[str, float]:
    return {k: float(v) for k, v in world.get("params", {}).items()}


def _run(params: dict[str, int], world: dict[str, Any], strategy_type: str, seed: int) -> dict[str, Any]:
    try:
        from app.strategy_lab.robustness.search import _evaluate_candidate
    except Exception:
        return {"total_return_pct": -100.0, "sharpe": -1.0, "max_drawdown_pct": -100.0, "trades": 0}
    return _evaluate_candidate(strategy_type, params, world, price_seed=seed)


def minimize(
    failure: dict[str, Any],
    *,
    max_iterations: int = 32,
    seed: int = 0,
) -> dict[str, Any]:
    params = dict(failure.get("parameters") or {})
    world = dict(failure.get("world_spec") or {})
    predicates = [
        ThresholdPredicate(
            metric=f["predicate"].rsplit("_", 1)[0],
            comparator=f["predicate"].rsplit("_", 1)[1],
            threshold=float(f["threshold"]),
        )
        for f in failure.get("failed_predicates", [])
    ]
    strategy_type = world.get("type") or world.get("strategy_type") or "sma_crossover"
    if not predicates:
        return {
            "original_failure": failure,
            "minimized_failure": failure,
            "delta": {"parameters": params, "world_params": _world_params(world)},
            "status": "completed",
            "iterations": 0,
        }

    fn_build = build_predicates(predicates)
    base_metrics = _run(params, world, strategy_type=strategy_type, seed=seed)
    base_hits = evaluate_predicates(base_metrics, fn_build)
    if not any(base_hits):
        return {
            "original_failure": failure,
            "minimized_failure": failure,
            "delta": {"parameters": params, "world_params": _world_params(world)},
            "status": "completed",
            "iterations": 0,
        }

    discovery = {
        "parameters": {k: {"baseline": v, "minimized": v} for k, v in params.items()},
        "world_params": {k: {"baseline": v, "minimized": v} for k, v in _world_params(world).items()},
    }
    iterations = 0
    minimize_failure = dict(failure)
    for idx in range(max_iterations):
        iterations += 1
        cur_params = {k: discovery["parameters"][k]["minimized"] for k in discovery["parameters"]}
        cur_world_params = {k: discovery["world_params"][k]["minimized"] for k in discovery["world_params"]}
        cur_world = dict(world)
        cur_world["params"] = cur_world_params
        metrics = _run(cur_params, cur_world, strategy_type=strategy_type, seed=seed + idx)
        hits = evaluate_predicates(metrics, fn_build)
        if not any(hits):
            minimize_failure = {
                "campaign_id": failure.get("campaign_id"),
                "evaluation_index": failure.get("evaluation_index"),
                "category": FailureCategory(failure.get("category", str(FailureCategory.TREND_REVERSAL))),
                "severity": str(failure.get("severity", "high")),
                "failed_predicates": [],
                "world_spec": cur_world,
                "parameters": cur_params,
                "metrics": metrics,
                "method": failure.get("method"),
                "replay_artifact_id": failure.get("replay_artifact_id"),
            }
            break
        for pred, hit in zip(predicates, hits, strict=True):
            key = pred.metric if hasattr(pred, "metric") else predicates[0].metric
            if key in cur_world_params and hit:
                current = float(cur_world_params[key])
                new_value = current * 0.5 if current != 0.0 else -0.05
                discovery["world_params"][key]["minimized"] = float(new_value)
            else:
                for p_key in discovery["parameters"]:
                    discovery["parameters"][p_key]["minimized"] = max(
                        1, int(float(discovery["parameters"][p_key]["minimized"]) * 0.8)
                    )

    minimized_evidence = FailureEvidence(
        observed=float(minimize_failure.get("metrics", {}).get("total_return_pct", 0.0)),
        threshold=float(failure.get("failed_predicates", [{}])[0].get("threshold", 0.0))
        if failure.get("failed_predicates")
        else 0.0,
        comparator="le" if failure.get("failed_predicates") else "le",
        metric=failure.get("failed_predicates", [{}])[0]
        .get("predicate", "total_return_pct_le")
        .rsplit("_", 1)[0]
        if failure.get("failed_predicates")
        else "total_return_pct",
    )
    record = FailureRecord(
        category=FailureCategory(failure.get("category", FailureCategory.TREND_REVERSAL)),
        severity=FailureSeverity.HIGH,
        evidence=minimized_evidence,
        candidate=failure,
        minimized_candidate=minimize_failure,
        replay_artifact_id=failure.get("replay_artifact_id"),
        suggestions=[],
        extra={"delta": discovery, "iterations": iterations},
    )
    return {
        "original_failure": failure,
        "minimized_failure": minimize_failure,
        "failure_record": record.__dict__,
        "delta": discovery,
        "status": "completed",
        "iterations": iterations,
    }
