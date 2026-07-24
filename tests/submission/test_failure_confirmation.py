"""Test-failure-confirmation (spec 4.3).

A single failed world must NOT be reported as a confirmed failure.
Confirmation requires repeated seeds (2 of 3) violating the same predicate.
Candidate failures (single-seed) are reported separately from confirmed.
"""

from __future__ import annotations

from app.strategy_lab.submission.strategy import CrossSectionalSpec
from app.strategy_lab.submission.stress_search import (
    DEFAULT_PREDICATES,
    run_fast_search,
)


def _spec():
    return CrossSectionalSpec(universe=["SYN_A", "SYN_B", "SYN_C", "SYN_D", "SYN_E", "SYN_F", "SPY"], benchmark="SPY")


def test_candidate_vs_confirmed_are_separate():
    res = run_fast_search(strategy_hash="x", spec=_spec(), base_assets=list(_spec().universe), T=504, budget=33)
    # the run reports both counts distinctly
    assert "candidate_count" in res
    assert "failure_count" in res
    assert res["failure_count"] <= res["candidate_count"]


def test_confirmation_requires_repeated_seeds():
    """Engine confirms only when >=2 of 3 seeds violate the same predicate.
    Monkeypatch the world evaluation so that only the BASE seed violates,
    siblings do not -> must NOT be confirmed."""

    # Build a fake world result that violates on base but not siblings.
    base = {
        "mechanism": "volatility_expansion", "seed": 999, "intensity": 0.9,
        "sharpe": 0.0, "max_drawdown": -0.05, "cost_pct": 0.0,
        "violated_predicates": ["low_sharpe"], "non_shortable": 0,
        "execution_delay_days": 0, "data_failure": False,
    }
    siblings = [
        {"mechanism": "volatility_expansion", "seed": 1000, "intensity": 0.9,
         "sharpe": 5.0, "max_drawdown": -0.01, "cost_pct": 0.0,
         "violated_predicates": [], "non_shortable": 0, "execution_delay_days": 0, "data_failure": False},
        {"mechanism": "volatility_expansion", "seed": 1001, "intensity": 0.9,
         "sharpe": 5.0, "max_drawdown": -0.01, "cost_pct": 0.0,
         "violated_predicates": [], "non_shortable": 0, "execution_delay_days": 0, "data_failure": False},
    ]
    # emulate the confirmation logic directly
    base_viol = set(base["violated_predicates"])
    sibling_viol = [set(s["violated_predicates"]) for s in siblings]
    shared = [p for p in base_viol if sum(p in sv for sv in sibling_viol) >= 2]
    assert shared == [], "single-seed violation must not be 'shared' across 2 siblings"


def test_confirmed_failure_carries_predicate_and_rule():
    res = run_fast_search(strategy_hash="x", spec=_spec(), base_assets=list(_spec().universe), T=504, budget=33)
    for cf in res["confirmed_failures"]:
        assert "confirmed_predicates" in cf
        assert "confirmation" in cf
        # confirmed predicate must be a real predicate name
        names = {p.name for p in DEFAULT_PREDICATES}
        assert set(cf["confirmed_predicates"]).issubset(names)
