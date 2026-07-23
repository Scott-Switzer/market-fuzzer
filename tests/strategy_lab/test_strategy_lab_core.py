from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.strategy_lab.api_lab import router as strategy_lab_router
from app.strategy_lab.compiler.planner import StrategyPlanner
from app.strategy_lab.compiler.clause_classifier import ClauseClassifier
from app.strategy_lab.dsl import (
    ClauseLedgerEntry,
    ClauseResolution,
    ClauseStatus,
    MacroGate,
    OrderedClause,
    RsiReversion,
    SmaCrossover,
    Strategy,
    ValueQualityLongShort,
)
from app.strategy_lab.service_lab import ApprovalService


def test_sma_strategy_hash_is_deterministic():
    strategy = Strategy(
        strategy_id="",
        family="sma_crossover",
        description="SMA crossover strategy.",
        description_original="SMA crossover strategy.",
        execution_policy=_exec_policy(),
        clauses=[OrderedClause(order=0, clause=SmaCrossover(fast=20, slow=50), clause_id="c_0")],
        clause_ledger=_ledger("SMA crossover strategy."),
    )
    first = strategy.ledger_hash
    second = Strategy.model_validate(json.loads(json.dumps(strategy.model_dump(mode="json")))).ledger_hash
    assert first == second
    assert len(first) == 64


def test_approval_locks_strategy_and_returns_hash():
    strategy = Strategy(
        strategy_id="",
        family="value_quality_long_short",
        description="Long top 10%, short bottom 10%, beta neutral.",
        description_original="Long top 10%, short bottom 10%, beta neutral.",
        execution_policy=_exec_policy(),
        clauses=[
            OrderedClause(
                order=0,
                clause=ValueQualityLongShort(
                    long_top_n=10, short_bottom_n=10, beta_neutralize=True, hedge_universe="SP500"
                ),
                clause_id="c_0",
            )
        ],
        clause_ledger=_ledger("Long top 10%, short bottom 10%, beta neutral."),
    )
    approval = ApprovalService.lock(strategy, actor="tester")
    assert approval["status"] == "approved"
    assert approval["strategy_id"] == strategy.ledger_hash
    assert approval["canonical_hash"] == strategy.ledger_hash


def test_plain_text_planner_returns_strategy_hash():
    result = StrategyPlanner.plan_from_text(
        "Rank the S&P 500 by earnings yield and gross profitability and go long the top 10% and short the bottom 10%."
    )
    assert "strategy_hash" in result
    assert len(result["strategy_hash"]) == 64
    assert result["spec"]["family"] == "value_quality_long_short"
    assert result["spec"]["clause_ledger"][0]["status"] == ClauseStatus.SUPPORTED_AND_COMPILED.value


def test_macro_gate_clause_roundtrip():
    clause = MacroGate(
        indicator="volatility_regime",
        threshold=0.25,
        retract_by_bar=1,
        action_on_breach="hold",
        note="risk-off gate",
    )
    strategy = Strategy(
        strategy_id="",
        family="macro_gated_risk_off",
        description="Macro gated risk-off strategy.",
        description_original="Macro gated risk-off strategy.",
        execution_policy=_exec_policy(),
        clauses=[OrderedClause(order=0, clause=clause, clause_id="c_0")],
        clause_ledger=_ledger("Macro gated risk-off strategy."),
    )
    assert strategy.ledger_hash
    payload = json.loads(json.dumps(strategy.model_dump(mode="json")))
    restored = Strategy.model_validate(payload)
    assert restored.ledger_hash == strategy.ledger_hash


def test_strategy_lab_router_exposes_compile_and_approve():
    app = FastAPI()
    app.include_router(strategy_lab_router, prefix="/api/strategy-lab", tags=["strategy-lab"])
    client = TestClient(app)

    compile_response = client.post(
        "/api/strategy-lab/compile", json={"description": "SMA crossover fast 20 slow 50"}
    )
    assert compile_response.status_code == 200
    compiled = compile_response.json()
    assert compiled["ok"] is True
    assert "strategy_hash" in compiled
    assert compiled["spec"]["family"] == "sma_crossover"

    approve_response = client.post(
        "/api/strategy-lab/approve", json={"spec": compiled["spec"], "actor": "tester"}
    )
    assert approve_response.status_code == 200
    approved = approve_response.json()
    assert approved["ok"] is True
    assert approved["approval"]["status"] == "approved"
    assert approved["strategy_id"] == compiled["strategy_hash"]


def test_approve_blocks_ambiguous():
    app = FastAPI()
    app.include_router(strategy_lab_router, prefix="/api/strategy-lab", tags=["strategy-lab"])
    client = TestClient(app)
    payload = {
        "description": "Buy something sketchy and good.",
    }
    compile_response = client.post("/api/strategy-lab/compile", json=payload)
    compiled = compile_response.json()
    assert compiled["ok"] is True

    spec = compiled["spec"]
    for entry in spec.setdefault("clause_ledger", []):
        if entry.get("status") == ClauseStatus.AMBIGUOUS_REQUIRES_RESOLUTION.value:
            entry["user_resolution"] = ClauseResolution.PENDING.value

    response = client.post("/api/strategy-lab/approve", json={"spec": spec, "actor": "tester"})
    assert response.status_code == 422


def test_approve_blocks_unsupported():
    app = FastAPI()
    app.include_router(strategy_lab_router, prefix="/api/strategy-lab", tags=["strategy-lab"])
    client = TestClient(app)
    payload = {
        "description": "Buy when there is positive twitter sentiment for crypto.",
    }
    compile_response = client.post("/api/strategy-lab/compile", json=payload)
    compiled = compile_response.json()
    assert compiled["ok"] is True

    spec = compiled["spec"]
    for entry in spec.setdefault("clause_ledger", []):
        if entry.get("status") == ClauseStatus.UNSUPPORTED_SAVED_FOR_RESEARCH.value:
            entry["user_resolution"] = ClauseResolution.PENDING.value

    response = client.post("/api/strategy-lab/approve", json={"spec": spec, "actor": "tester"})
    assert response.status_code == 422


def test_rejected_unsafe_clause_status():
    classification = ClauseClassifier.classify("all-in leverage is fine here")
    assert classification["status"] == ClauseStatus.REJECTED_UNSAFE_OR_INVALID.value
    ledger = ClauseClassifier.build_clause("c_0", "all-in leverage is fine here")
    assert ledger["status"] == ClauseStatus.REJECTED_UNSAFE_OR_INVALID.value


def test_deterministic_fallback_compiler_wired():
    first = StrategyPlanner.plan_from_text("SMA crossover fast 20 slow 50")
    second = StrategyPlanner.plan_from_text("SMA crossover fast 20 slow 50")
    assert first["strategy_hash"] == second["strategy_hash"]
    assert first["spec"]["family"] == "sma_crossover"
    assert first["spec"]["clause_ledger"][0]["status"] == ClauseStatus.SUPPORTED_AND_COMPILED.value


def test_strategy_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        Strategy.model_validate(
            {
                "family": "sma_crossover",
                "execution_policy": _exec_policy_dict(),
                "clauses": [
                    {
                        "order": 0,
                        "clause": {"kind": "SmaCrossover", "fast": 20, "slow": 50},
                        "clause_id": "c_0",
                    }
                ],
                "clause_ledger": [],
                "unexpected_field": "forbidden",
            }
        )


def test_rsi_reversion_roundtrip():
    strategy = Strategy(
        strategy_id="",
        family="rsi_reversion",
        description="RSI reversion strategy.",
        description_original="RSI reversion strategy.",
        execution_policy=_exec_policy(),
        clauses=[OrderedClause(order=0, clause=RsiReversion(period=14, oversold=30.0, overbought=70.0), clause_id="c_0")],
        clause_ledger=_ledger("RSI reversion strategy."),
    )
    restored = Strategy.model_validate(json.loads(json.dumps(strategy.model_dump(mode="json"))))
    assert restored.ledger_hash == strategy.ledger_hash


def test_value_quality_long_short_pure_short_rejected():
    with pytest.raises(ValidationError):
        Strategy(
            strategy_id="",
            family="value_quality_long_short",
            description="Pure short strategy.",
            description_original="Pure short strategy.",
            execution_policy=_exec_policy(),
            clauses=[
                OrderedClause(
                    order=0,
                    clause=ValueQualityLongShort(long_top_n=0, short_bottom_n=10),
                    clause_id="c_0",
                )
            ],
            clause_ledger=[],
        )


def _exec_policy():
    from app.strategy_lab.dsl import ExecutionPolicy, FillTarget, TimeInForce, Uint

    return ExecutionPolicy(
        fill_target=FillTarget.mid, max_order_qty=Uint(1000), time_in_force=TimeInForce.day
    )


def _exec_policy_dict() -> dict:
    return {
        "fill_target": "mid",
        "max_order_qty": 1000,
        "time_in_force": "day",
    }


def _ledger(original_text: str) -> list[ClauseLedgerEntry]:
    return [
        ClauseLedgerEntry(
            clause_id="c_0",
            original_text=original_text,
            normalized_text=original_text,
            status=ClauseStatus.SUPPORTED_AND_COMPILED,
            reason=None,
            user_resolution=ClauseResolution.APPROVED,
            compiler_confidence=0.8,
        )
    ]
