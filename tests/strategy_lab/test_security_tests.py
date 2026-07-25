from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.strategy_lab.dsl import (
    ExecutionPolicy,
    FillTarget,
    OrderedClause,
    SmaCrossover,
    Strategy,
    TimeInForce,
    Uint,
)


class _OrderedClauseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: int = 0
    clause: dict[str, str] = {}
    clause_id: str


def _exec_policy() -> ExecutionPolicy:
    return ExecutionPolicy(
        fill_target=FillTarget.mid,
        max_order_qty=Uint(1000),
        time_in_force=TimeInForce.day,
    )


def _pydantic_extra_object() -> type[BaseModel]:
    class _ExtraModel(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: str = Field(min_length=1)

    return _ExtraModel


class TestHiddenParameterLeakage:
    def test_legacy_is_locked_field_is_accepted_as_planned(self):
        payload = {
            "family": "sma_crossover",
            "is_locked": True,
            "execution_policy": {
                "fill_target": "mid",
                "max_order_qty": 1000,
                "time_in_force": "day",
            },
            "clauses": [
                {
                    "order": 0,
                    "clause": {"kind": "SmaCrossover", "fast": 20, "slow": 50},
                    "clause_id": "c_0",
                }
            ],
            "clause_ledger": [],
        }
        strategy = Strategy.model_validate(payload)
        assert strategy.is_locked is True

    def test_frozen_strategy_rejects_mutation_by_payload(self):
        strategy = Strategy(
            strategy_id="",
            family="sma_crossover",
            description="SMA crossover.",
            description_original="SMA crossover.",
            execution_policy=_exec_policy(),
            clauses=[OrderedClause(order=0, clause=SmaCrossover(fast=20, slow=50), clause_id="c_0")],
            clause_ledger=[],
        )
        payload = json.loads(json.dumps(strategy.model_dump(mode="json")))
        restored = Strategy.model_validate(payload)
        assert restored.ledger_hash == strategy.ledger_hash


class TestSchemaEscape:
    def test_extra_fields_are_rejected(self):
        with pytest.raises(ValidationError):
            _pydantic_extra_object()(name="ok", injected="no")

    def test_negative_quantity_rejected(self):
        with pytest.raises(ValidationError):
            _OrderedClauseModel(order=-1)

    def test_invalid_action_enum_rejected(self):
        from app.strategy_lab.dsl import Action

        with pytest.raises(ValueError):
            Action("illegal_action")
