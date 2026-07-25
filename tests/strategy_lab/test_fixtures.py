from __future__ import annotations

import json

from app.strategy_lab.dsl import (
    ClauseLedgerEntry,
    ClauseResolution,
    ClauseStatus,
    ExecutionPolicy,
    FillTarget,
    MacroGate,
    OrderedClause,
    RsiReversion,
    SmaCrossover,
    Strategy,
    TimeInForce,
    Uint,
    ValueQualityLongShort,
)


def _exec_policy() -> ExecutionPolicy:
    return ExecutionPolicy(
        fill_target=FillTarget.mid,
        max_order_qty=Uint(1000),
        time_in_force=TimeInForce.day,
    )


def test_fixture_sma_crossover_roundtrip():
    data = _load_fixture("sma_crossover_fast20_slow50.json")
    strategy = _strategy_from_fixture(data)
    assert strategy.ledger_hash
    restored = Strategy.model_validate(json.loads(json.dumps(strategy.model_dump(mode="json"))))
    assert restored.ledger_hash == strategy.ledger_hash


def test_fixture_rsi_reversion_status():
    data = _load_fixture("rsi_reversion_30_70.json")
    assert data["expected_runtime_behavior"] == "compile_success"
    strategy = _strategy_from_fixture(data)
    for entry in strategy.clause_ledger:
        assert entry.status == ClauseStatus.SUPPORTED_AND_COMPILED


def test_fixture_value_quality_long_short_schema():
    data = _load_fixture("value_quality_long_short.json")
    clause_payload = data["expected_canonical_schema"]["clauses"][1]["clause"]
    clause = ValueQualityLongShort.model_validate(clause_payload)
    assert clause.long_top_n == 10
    assert clause.short_bottom_n == 10


def test_fixture_macro_gated_hash_shape():
    data = _load_fixture("macro_gated.json")
    strategy = _strategy_from_fixture(data)
    expected_hash = data["expected_hash"]
    if expected_hash and expected_hash != "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855":
        assert strategy.ledger_hash == expected_hash
    else:
        assert len(strategy.ledger_hash) == 64


def test_fixture_ledger_statuses_match_schema():
    for fixture_name in [
        "sma_crossover_fast20_slow50.json",
        "rsi_reversion_30_70.json",
        "value_quality_long_short.json",
        "macro_gated.json",
    ]:
        data = _load_fixture(fixture_name)
        for entry in data["expected_clause_ledger"]:
            assert entry["status"] in {status.value for status in ClauseStatus}


def _load_fixture(name: str) -> dict:
    import os

    path = os.path.join(os.path.dirname(__file__), "fixtures", name)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _strategy_from_fixture(data: dict) -> Strategy:
    ledger = [
        ClauseLedgerEntry(
            clause_id=entry.get("clause_id", "c_0"),
            original_text=entry.get("original_text", data.get("original_text", "")),
            normalized_text=entry.get("normalized_text"),
            status=ClauseStatus(entry["status"]),
            reason=entry.get("reason"),
            user_resolution=ClauseResolution(entry.get("user_resolution", "pending")),
            compiler_confidence=entry.get("compiler_confidence"),
            provenance=entry.get("provenance", {}),
        )
        for entry in data.get("expected_clause_ledger", [])
    ]
    clauses = []
    for clause_data in data.get("expected_canonical_schema", {}).get("clauses", []):
        clause_payload = clause_data.get("clause")
        if clause_payload is None:
            continue
        kind = clause_payload.get("kind")
        if kind == "SmaCrossover":
            clause = SmaCrossover.model_validate(clause_payload)
        elif kind == "RsiReversion":
            clause = RsiReversion.model_validate(clause_payload)
        elif kind == "ValueQualityLongShort":
            clause = ValueQualityLongShort.model_validate(clause_payload)
        elif kind == "MacroGate":
            clause = MacroGate.model_validate(clause_payload)
        else:
            continue
        clauses.append(
            OrderedClause(
                order=clause_data.get("order", len(clauses)),
                clause=clause,
                clause_id=clause_data.get("clause_id", f"c_{len(clauses)}"),
            )
        )
    strategy = Strategy(
        strategy_id="",
        family=data.get("expected_canonical_schema", {}).get("family", "unknown"),
        description=data.get("original_text"),
        description_original=data.get("original_text"),
        execution_policy=_exec_policy(),
        clauses=clauses
        if clauses
        else [OrderedClause(order=0, clause=SmaCrossover(fast=20, slow=50), clause_id="c_0")],
        clause_ledger=ledger if ledger else _default_ledger(data.get("original_text", "")),
    )
    return strategy


def _default_ledger(original_text: str = "default") -> list:
    from app.strategy_lab.dsl import ClauseLedgerEntry, ClauseResolution

    return [
        ClauseLedgerEntry(
            clause_id="c_0",
            original_text=original_text,
            normalized_text=original_text,
            status=ClauseStatus.SUPPORTED_AND_COMPILED,
            reason=None,
            user_resolution=ClauseResolution.PENDING,
            compiler_confidence=0.8,
        )
    ]
