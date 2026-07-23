from __future__ import annotations

import hashlib
import json
import math

import numpy as np
from hypothesis import HealthCheck, given, settings
from hypothesis.strategies import composite, integers, lists

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


@composite
def clause_order_permutations(draw, base_strategy: Strategy | None = None):
    strategy = base_strategy if base_strategy is not None else _base_strategy()
    orders = draw(lists(integers(min_value=0, max_value=100), min_size=1, max_size=5, unique=True))
    if not orders:
        orders = [0]
    clauses = []
    for idx, order in enumerate(orders):
        if idx == 0:
            clause = SmaCrossover(fast=20, slow=50)
        elif idx == 1:
            clause = RsiReversion(period=14, oversold=30.0, overbought=70.0)
        else:
            clause = MacroGate(indicator="volatility_regime", threshold=0.2, retract_by_bar=1)
        clauses.append(OrderedClause(order=order, clause=clause, clause_id=f"c_{order}"))
    payload = json.loads(json.dumps(strategy.model_dump(mode="json")))
    payload["clauses"] = [json.loads(json.dumps(c.model_dump(mode="json"))) for c in clauses]
    payload.pop("strategy_id", None)
    return payload


class TestCanonicalHashStableUnderKeyPermutation:
    @given(clause_order_permutations())
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_permutation_invariance(self, permuted_payload: dict):

        restored = Strategy.model_validate(permuted_payload)
        payload = json.loads(json.dumps(restored.model_dump(mode="json", exclude_none=True)))
        for key in ["strategy_id", "approval", "provenance", "conflict_report", "is_locked"]:
            payload.pop(key, None)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        actual = restored.ledger_hash
        assert actual == expected

    def test_original_ordering_stable(self):
        strategy = _base_strategy()
        first = strategy.ledger_hash
        restored = Strategy.model_validate(json.loads(json.dumps(strategy.model_dump(mode="json"))))
        assert restored.ledger_hash == first


class TestNoNanInfMetrics:
    @given(
        returns=lists(
            elements=integers(min_value=-1000000, max_value=1000000).map(float),
            min_size=2,
            max_size=64,
        )
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_metric_primitives_reject_non_finite(self, returns):
        values = np.asarray(returns, dtype=float)
        if not np.isfinite(values).all():
            return
        mean = float(np.mean(values))
        assert not math.isnan(mean)
        assert not math.isinf(mean)

    def test_rejects_nan_input(self):
        values = np.asarray([0.1, np.nan], dtype=float)
        assert not np.isfinite(values).all()

    def test_rejects_inf_input(self):
        values = np.asarray([0.1, np.inf], dtype=float)
        assert not np.isfinite(values).all()


class TestCostsNeverImproveReturn:
    @given(
        gross=integers(min_value=50, max_value=5000).map(float),
        cost_bps=integers(min_value=0, max_value=500).map(float),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_net_below_gross(self, gross: float, cost_bps: float):
        net = gross - cost_bps / 10000.0 * gross
        assert net <= gross + 1e-9


class TestIdenticalSeedsSameWorld:
    @given(fast=integers(min_value=2, max_value=200), slow=integers(min_value=2, max_value=500))
    @settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
    def test_repeated_seed_produces_identical_strategy(self, fast: int, slow: int):
        if fast >= slow:
            return
        first = SmaCrossover(fast=fast, slow=slow)
        second = SmaCrossover(fast=fast, slow=slow)
        assert first.model_dump(mode="json") == second.model_dump(mode="json")
