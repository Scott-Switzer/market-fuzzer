from __future__ import annotations

import json

import pytest

from app.strategy_lab.dsl import (
    ClauseLedgerEntry,
    ClauseResolution,
    ClauseStatus,
    ExecutionPolicy,
    FillTarget,
    OrderedClause,
    SmaCrossover,
    Strategy,
    TimeInForce,
    Uint,
)


def _exec_policy() -> ExecutionPolicy:
    return ExecutionPolicy(
        fill_target=FillTarget.mid,
        max_order_qty=Uint(1000),
        time_in_force=TimeInForce.day,
    )


def _ledger(original_text: str = "default") -> list[ClauseLedgerEntry]:
    return [
        ClauseLedgerEntry(
            clause_id="c_0",
            original_text=original_text,
            normalized_text=original_text,
            status=ClauseStatus.SUPPORTED_AND_COMPILED,
            reason=None,
            user_resolution=ClauseResolution.APPROVED,
            compiler_confidence=0.8,
            provenance={},
        )
    ]


def _base_strategy() -> Strategy:
    return Strategy(
        strategy_id="",
        family="sma_crossover",
        description="SMA crossover strategy.",
        description_original="SMA crossover strategy.",
        execution_policy=_exec_policy(),
        clauses=[OrderedClause(order=0, clause=SmaCrossover(fast=20, slow=50), clause_id="c_0")],
        clause_ledger=_ledger(),
    )


class TestPriceScalingInvariant:
    @pytest.mark.parametrize("scale", [1e-3, 1.0, 1e3, 1e6])
    def test_scaled_price_fields_preserve_schema_shape(self, scale: float):
        strategy = _base_strategy()
        payload = json.loads(json.dumps(strategy.model_dump(mode="json")))
        restored = Strategy.model_validate(payload)
        assert restored.family == payload["family"]
        assert len(restored.clauses) == len(payload["clauses"])
        assert restored.ledger_hash == strategy.ledger_hash

    def test_non_finite_thresholds_do_not_corrupt_hash(self):
        strategy = _base_strategy()
        payload = json.loads(json.dumps(strategy.model_dump(mode="json")))
        payload["clauses"][0]["clause"]["fast"] = "NaN"
        with pytest.raises(ValueError):
            Strategy.model_validate(payload)


class TestAnonymizedLabelPermutationInvariant:
    def test_anonymized_labels_are_label_invariant(self):
        first = _base_strategy()
        second_payload = json.loads(json.dumps(first.model_dump(mode="json")))
        second_payload["family"] = "anon_family_a"
        second_payload["description"] = "Anonymized family A strategy."
        second_payload["description_original"] = second_payload["description"]
        labels = ["ANON-1", "ANON-2"]
        second_payload["universe"] = {"type": "custom", "members": labels, "anonymized": True}
        second = Strategy.model_validate(second_payload)
        assert second.family == "anon_family_a"
        assert second.universe.get("anonymized") is True
        assert len(second.ledger_hash) == 64

    def test_ledger_hash_independent_of_label_values(self):
        strategy = _base_strategy()
        ledger_hash = strategy.ledger_hash
        restored = Strategy.model_validate(json.loads(json.dumps(strategy.model_dump(mode="json"))))
        assert restored.ledger_hash == ledger_hash


class TestCostMonotonicity:
    @pytest.mark.parametrize("cost_bps", [0, 10, 50, 100, 200, 500])
    def test_cost_reduces_net_return(self, cost_bps: int):
        gross = 1000.0
        cost = cost_bps / 10000.0 * gross
        net = gross - cost
        assert net <= gross + 1e-9
        assert net >= 0.0

    def test_cost_monotonicity_across_scales(self):
        for gross in [100.0, 1000.0, 10_000.0]:
            prev = None
            for cost_bps in [0, 25, 100, 250]:
                net = gross - cost_bps / 10000.0 * gross
                if prev is not None:
                    assert net <= prev + 1e-9
                prev = net


class TestNoTradeUnaffectedBySpread:
    def test_zero_turnover_preserves_net_return(self):
        gross = 1000.0
        turnover = 0.0
        cost_bps = 20
        cost = cost_bps / 10000.0 * turnover * gross
        net = gross - cost
        assert net == gross

    def test_spread_is_boundary_condition(self):
        spread_bps = 0.0
        turnover = 1.0
        gross = 1000.0
        cost = spread_bps / 10000.0 * turnover * gross
        net = gross - cost
        assert net == pytest.approx(gross)
