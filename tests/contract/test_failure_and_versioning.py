"""Tests for failure contracts + strategy versioning/approval (integrity gates 14,15,19)."""

from __future__ import annotations

import pytest

from app.domain.failure import AdjacentPass, ConfirmedFailure, MinimizedBoundary, Severity
from app.domain.strategy_spec import Clause, ClauseState, StrategySpec, StrategyType
from app.domain.strategy_version import ApprovalState, StrategyVersion


def _spec(**ov) -> StrategySpec:
    kw: dict[str, object] = {
        "name": "S",
        "original_thesis": "top momentum names monthly",
        "strategy_type": StrategyType.CROSS_SECTIONAL_FACTOR,
        "universe": ["AAPL", "MSFT", "NVDA"],
        "benchmark": "SPY",
    }
    kw.update(ov)
    return StrategySpec(**kw)  # type: ignore[arg-type]


# --- failure / minimization invariants ---
def test_minimized_boundary_invariant_holds():
    mb = MinimizedBoundary(
        parameter="borrow_bps",
        minimized_value=200.0,
        passing_lower_bound=150.0,
        still_fails=True,
        violated_predicates=["low_sharpe"],
    )
    assert mb.check_invariant()


def test_minimized_boundary_invariant_violation_detected():
    # A minimizer returning a passing case as the "minimized failure" (gate 14).
    mb = MinimizedBoundary(
        parameter="borrow_bps",
        minimized_value=100.0,
        passing_lower_bound=150.0,  # minimized <= passing -> invalid
        still_fails=True,
    )
    assert not mb.check_invariant()


def test_minimized_boundary_no_fail_is_ok():
    mb = MinimizedBoundary(
        parameter="borrow_bps", minimized_value=500.0, passing_lower_bound=500.0, still_fails=False
    )
    assert mb.check_invariant()


def test_adjacent_pass_absent_is_honest():
    ap = AdjacentPass(found=False)
    assert ap.found is False
    assert ap.value is None


def test_confirmed_failure_carries_predicate_and_seed_agreement():
    cf = ConfirmedFailure(
        failure_id="f1",
        strategy_hash="h",
        world_hash="w",
        mechanism="borrow_cost_increase",
        intensity=0.8,
        violated_predicates=["low_sharpe", "high_drawdown"],
        seed_agreement="2 of 3",
        severity=Severity.HIGH,
    )
    assert cf.violated_predicates
    assert cf.seed_agreement == "2 of 3"


# --- strategy versioning / approval ---
def test_version_from_spec_carries_hash():
    s = _spec()
    v = StrategyVersion.from_spec(s)
    assert v.canonical_hash == s.canonical_hash
    assert v.state == ApprovalState.DRAFT


def test_lock_executable_spec_succeeds_and_preserves_hash():
    s = _spec()
    v = StrategyVersion.from_spec(s).lock(approved_by="scott")
    assert v.is_locked
    assert v.approved_by == "scott"
    assert v.canonical_hash == s.canonical_hash  # gate 19: hash unchanged by approval


def test_cannot_lock_non_executable_spec():
    s = _spec(clauses=[Clause(id="c1", original_text="x", state=ClauseState.UNRESOLVED)])
    v = StrategyVersion.from_spec(s)
    with pytest.raises(ValueError, match="non-executable"):
        v.lock(approved_by="scott")


def test_lock_detects_hash_tamper():
    s = _spec()
    v = StrategyVersion.from_spec(s)
    tampered = v.model_copy(update={"canonical_hash": "0" * 64})
    with pytest.raises(ValueError, match="does not match"):
        tampered.lock(approved_by="scott")
