from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from fastapi import APIRouter, HTTPException

from app.strategy_lab.compiler.planner import StrategyPlanner
from app.strategy_lab.dsl import ClauseResolution, ClauseStatus, Strategy
from app.strategy_lab.service_lab import ApprovalService

router = APIRouter()


@router.post("/compile")
def compile_strategy(body: dict[str, Any]) -> dict[str, Any]:
    raw_text = body.get("description", "")
    result = StrategyPlanner.plan_from_text(raw_text)
    return {"ok": True, **result}


@router.post("/approve")
def approve_strategy(body: dict[str, Any]) -> dict[str, Any]:
    spec = body.get("spec")
    if not spec:
        return {"ok": False, "error": "missing spec"}

    strategy = Strategy.model_validate(spec)
    for entry in strategy.clause_ledger:
        if entry.status in (
            ClauseStatus.AMBIGUOUS_REQUIRES_RESOLUTION,
            ClauseStatus.UNSUPPORTED_SAVED_FOR_RESEARCH,
        ):
            if entry.user_resolution != ClauseResolution.APPROVED:
                raise HTTPException(422, f"Cannot approve strategy with unresolved clause: {entry.clause_id}")

    approval = ApprovalService.lock(spec, actor=body.get("actor", "user"))
    strategy = Strategy.model_validate(spec)
    return {"ok": True, "approval": approval, "strategy_id": strategy.ledger_hash}


try:
    from app.break_test.service import run_break_test
    from app.strategy_lab.robustness.minimizer import minimize
    from app.strategy_lab.robustness.suggestions import EvidenceLinkedSuggestionEngine

    _STRATEGY_LAB_DEPS_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency guard
    _STRATEGY_LAB_DEPS_AVAILABLE = False


def _public_failure_summary(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result:
        return result
    safe: dict[str, Any] = {}
    for key, value in result.items():
        if key == "first_failure_scenario" and isinstance(value, dict):
            safe[key] = {
                "scenario": value.get("scenario"),
                "margin": value.get("margin"),
                "failure": value.get("failure"),
            }
        else:
            safe[key] = value
    return safe


@router.post("/backtests")
def strategy_lab_backtest(body: dict[str, Any]) -> dict[str, Any]:
    if not _STRATEGY_LAB_DEPS_AVAILABLE:
        raise HTTPException(503, "strategy lab backtest dependencies are unavailable")
    try:
        closes = [float(x) for x in (body.get("closes") or [])]
        if len(closes) < 80:
            raise ValueError("closes must contain at least 80 prices")
        strategy_type = body.get("strategy_type", "sma_crossover")
        params = body.get("params") or {}
        worlds_per_regime = int(body.get("worlds_per_regime", 10))
        forward_mode = body.get("forward_mode", "gbm")
        return run_break_test(
            closes,
            strategy_type=strategy_type,
            params=params,
            worlds_per_regime=worlds_per_regime,
            forward_mode=forward_mode,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"strategy lab backtest failed: {exc}") from exc


@router.post("/sealed/run")
def strategy_lab_sealed_run(body: dict[str, Any]) -> dict[str, Any]:
    if not _STRATEGY_LAB_DEPS_AVAILABLE:
        raise HTTPException(
            503,
            "strategy lab sealed-run dependency is unavailable in the isolated server",
        )
    try:
        from app.strategy_lab.campaigns.campaign_engine import (
            SealedCampaignEngineV1,
        )

        payload = body if isinstance(body, dict) else {}
        campaign_payload = _coerce_campaign_payload(payload)
        engine = SealedCampaignEngineV1(failing_strategy_backend="deterministic_product_fixture")
        campaign = engine.prepare_campaign(campaign_payload)
        if payload.get("strategy_spec"):
            engine.register_strategy_artifact(
                {
                    "digest": strategy_digest(payload["strategy_spec"]),
                    "raw": redact_strategy_spec(payload["strategy_spec"]),
                }
            )
        campaign = engine.deterministic_evaluation(campaign)
        campaign = engine.run_baseline_search(campaign)
        if campaign.state == "baseline" and (campaign.baseline_result or {}).get("failures"):
            campaign = engine.run_broad_search(campaign)
            if campaign.state == "broad" and (campaign.broad_search_evidence or {}).get("failures"):
                campaign = engine.run_targeted_search(campaign)
                if campaign.state in {"targeted", "confirmed_failure"}:
                    campaign = engine.confirm_failure(campaign)

        public_document = engine.public_document(campaign)
        result = {
            "ok": True,
            "campaign": public_document,
            "warnings": [warning for warning in _collect_warnings(campaign) if warning],
            "replay_artifacts": _build_replay_artifacts(public_document),
            "redactions": ["hidden_family_allocation", "secret_seed_material", "family_labels"],
        }
        if campaign.state == "confirmed_failure":
            failure_result = _public_failure_summary(campaign.targeted_search_evidence) or {}
            best_minimization = failure_result.get("minimized_scenario") or (
                failure_result.get("first_failure_scenario") or {}
            )
            result["minimization"] = {
                "minimized_scenario": best_minimization,
                "suggestions": [
                    "Increase liquidity or reduce latency_ms before re-running the sealed campaign.",
                    "Validate combined high-volatility and high-latency stress paths in operator preflight.",
                ],
            }
        return result
    except Exception as exc:
        raise HTTPException(500, f"sealed run failed: {exc}") from exc


def _coerce_campaign_payload(payload: dict[str, Any]) -> dict[str, Any]:
    has_all_families = all(key in payload for key in ("same_family_ids", "holdout_family_ids"))
    if has_all_families:
        coerced = dict(payload)
    else:
        coerced = {
            "campaign_id": payload.get("campaign_id") or payload.get("strategy_id") or "campaign-unknown",
            "name": payload.get("name") or payload.get("strategy_name") or "Sealed campaign",
            "description": payload.get("description") or "",
            "same_family_ids": list(payload.get("same_family_ids", ["heterogeneous_agent_v1"])),
            "holdout_family_ids": list(
                payload.get("holdout_family_ids", ["regime_switching_point_process_v1"])
            ),
            "worlds_per_family": int(payload.get("worlds_per_family", 1)),
            "hidden_parameter_ranges": [
                {
                    "family_id": range_item.get("family_id", "heterogeneous_agent_v1"),
                    "parameter_name": str(range_item["parameter_name"]),
                    "lower_bound": float(range_item["lower_bound"]),
                    "upper_bound": float(range_item["upper_bound"]),
                }
                for range_item in payload.get("hidden_parameter_ranges", [])
            ],
        }
    if "same_family_ids" in coerced:
        coerced["same_family_ids"] = tuple(coerced["same_family_ids"])
    if "holdout_family_ids" in coerced:
        coerced["holdout_family_ids"] = tuple(coerced["holdout_family_ids"])
    if "campaign_id" in coerced:
        coerced["campaign_id"] = str(coerced["campaign_id"])
    if "name" in coerced:
        coerced["name"] = str(coerced["name"])
    description = coerced.get("description")
    if description is not None and not isinstance(description, str):
        coerced["description"] = str(description)
    coerced["scoring_policy_digest"] = _digest({"policy": "sealed-campaign-v1", "generated_at": _now_iso()})
    return coerced


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def redact_strategy_spec(spec: dict[str, Any]) -> dict[str, Any]:
    redacted = copy.deepcopy(spec)
    hidden_keys = {
        "family_id",
        "holdout_family_ids",
        "same_family_ids",
        "hidden_parameter_ranges",
        "parameter_overrides",
        "seed",
    }
    if isinstance(redacted, dict):
        redacted = {key: ("***" if key in hidden_keys else value) for key, value in redacted.items()}
    return redacted


def strategy_digest(spec: dict[str, Any]) -> str:
    safe = redact_strategy_spec(spec)
    return _digest(safe)


def _collect_warnings(campaign) -> list[str]:
    warnings: list[str] = []
    try:
        if campaign.failure_confirmation and campaign.failure_confirmation.get("failure_rate"):
            warnings.append(
                "sealed campaign confirmed at least one failing evaluation; results are not a production guarantee."
            )
        if campaign.targeted_search_evidence and campaign.targeted_search_evidence.get("stage") == "targeted":
            warnings.append("targeted search explored stress neighbors around failure boundaries.")
    except Exception:
        pass
    return warnings


def _build_replay_artifacts(public_document: dict[str, Any]) -> dict[str, Any]:
    baseline = public_document.get("baseline_result") or {}
    broad = public_document.get("broad_search_evidence") or {}
    targeted = public_document.get("targeted_search_evidence") or {}
    failure = public_document.get("failure_confirmation") or {}
    return {
        "manifest_id": public_document.get("public_document", {}).get("generator_bundle_digest", ""),
        "world_replay_token": public_document.get("commitment_digest", ""),
        "replay_surface": {
            "baseline_evaluated": baseline.get("evaluated"),
            "broad_evaluated": broad.get("evaluated"),
            "targeted_evaluated": targeted.get("evaluated"),
            "failure_rate": failure.get("failure_rate"),
            "passing_neighbor": failure.get("passing_neighbor"),
        },
        "replay_policy": {
            "max_replay_budget_worlds": 64,
            "deterministic_seed": True,
            "requires_revealed_policy": True,
        },
    }


@router.post("/replay/minimize")
def strategy_lab_minimize(body: dict[str, Any]) -> dict[str, Any]:
    if not _STRATEGY_LAB_DEPS_AVAILABLE:
        raise HTTPException(503, "strategy lab replay dependencies are unavailable")
    failure = body.get("failure")
    if not isinstance(failure, dict):
        raise HTTPException(422, "failure payload is required")
    try:
        minimized = minimize(failure, max_iterations=24, seed=body.get("seed", 0))
        suggestions = EvidenceLinkedSuggestionEngine.suggest(failure)
        return {"minimized": minimized, "suggestions": suggestions}
    except Exception as exc:
        raise HTTPException(500, f"strategy lab minimization failed: {exc}") from exc


@router.post("/evidence/export")
def strategy_lab_export(body: dict[str, Any]) -> dict[str, Any]:
    payload = body.get("payload") if isinstance(body, dict) else None
    if not isinstance(payload, dict):
        payload = {"scope": "strategy_validation_lab", "inputs": body}
    envelope = {
        "scope": "strategy_validation_lab",
        "exported_at": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
        "payload": payload,
        "validation": {
            "status": "available",
            "limitation": "Development fixture export. Do not use as live-trading approval.",
            "claim_boundary": "Strategy lab evidence packages never claim profitability or production safety.",
        },
        "legacy_routes_preserved": [
            "/",
            "/break-test",
            "/synthetic-market-world",
            "/strategy-stress-lab",
            "/arena",
            "/market-fuzzer",
        ],
    }
    return {"ok": True, "envelope": envelope}
