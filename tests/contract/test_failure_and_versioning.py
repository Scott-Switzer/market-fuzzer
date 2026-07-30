"""Tests for failure contracts + strategy versioning/approval (integrity gates 14,15,19)."""

from __future__ import annotations

import pytest

from app.domain.failure import AdjacentPass, ConfirmedFailure, MinimizedBoundary, Severity
from app.domain.strategy_spec import Clause, ClauseState, StrategySpec, StrategyType
from app.domain.strategy_version import ApprovedStrategyVersion, DraftStrategy

SUPPORTED = set(StrategyType) - {StrategyType.UNSUPPORTED}


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
def test_draft_carries_hash_and_stable_identity():
    s = _spec()
    d = DraftStrategy(spec=s)
    assert d.canonical_hash == s.compute_hash()
    # identity is a stable UUID, not the content hash
    assert d.strategy_id != d.canonical_hash
    assert d.version == 1


def test_next_version_preserves_identity():
    s = _spec()
    d = DraftStrategy(spec=s)
    d2 = d.next_version(_spec(frequency="weekly"))
    assert d2.strategy_id == d.strategy_id  # same logical strategy
    assert d2.version == 2
    assert d2.canonical_hash != d.canonical_hash  # different content


def test_approve_executable_spec_succeeds_and_preserves_hash():
    s = _spec()
    d = DraftStrategy(spec=s)
    approved = d.approve(approved_by="scott", supported_types=SUPPORTED)
    assert isinstance(approved, ApprovedStrategyVersion)
    assert approved.approved_by == "scott"
    assert approved.canonical_hash == s.compute_hash()  # hash unchanged by approval
    assert approved.strategy_id == d.strategy_id
    assert approved.verify()


def test_cannot_approve_non_executable_spec():
    s = _spec(clauses=[Clause(id="c1", original_text="x", state=ClauseState.UNRESOLVED)])
    d = DraftStrategy(spec=s)
    with pytest.raises(ValueError, match="non-executable"):
        d.approve(approved_by="scott", supported_types=SUPPORTED)


def test_approved_snapshot_reconstructs_and_detects_tamper():
    s = _spec()
    approved = DraftStrategy(spec=s).approve(approved_by="scott", supported_types=SUPPORTED)
    # round-trip
    rebuilt = approved.to_spec()
    assert rebuilt.compute_hash() == approved.canonical_hash
    assert rebuilt.strategy_id == approved.strategy_id
    # tamper the stored canonical JSON -> verify() False and to_spec() raises
    tampered = approved.model_copy(update={"canonical_json": approved.canonical_json.replace("AAPL", "TSLA")})
    assert not tampered.verify()
    with pytest.raises(ValueError, match="mismatch"):
        tampered.to_spec()


def test_approved_version_is_frozen():
    from pydantic import ValidationError

    approved = DraftStrategy(spec=_spec()).approve(approved_by="scott", supported_types=SUPPORTED)
    with pytest.raises(ValidationError):
        approved.canonical_hash = "0" * 64  # type: ignore[misc]
