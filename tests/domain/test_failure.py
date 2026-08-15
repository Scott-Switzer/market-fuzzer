"""P5 mathematical-trustworthiness: severity semantics + confirmation evidence.

Pure unit tests for the evidence-derived severity and the confirmation
lower-confidence-bound. These are the foundation the Failure Lab rests on:
severity must be COMPUTED from evidence, never asserted by default, and the
confirmation policy must report honest statistical evidence.
"""

from __future__ import annotations

from app.domain.failure import (
    CRITICAL_PREDICATES,
    Severity,
    compute_severity,
    confirmation_confidence,
)


def test_severity_low_for_small_unconfirmed_noncritical():
    # Small intensity, weak confirmation, non-critical predicate -> LOW.
    sev = compute_severity(
        intensity=0.1,
        confirmation_successes=1,
        confirmation_trials=3,
        violated_predicates=["sharpe"],
    )
    assert sev == Severity.LOW


def test_severity_critical_for_large_confirmed_critical_predicate():
    # Large shock + fully confirmed + critical predicate -> CRITICAL.
    sev = compute_severity(
        intensity=0.9,
        confirmation_successes=3,
        confirmation_trials=3,
        violated_predicates=["max_drawdown"],
    )
    assert sev == Severity.CRITICAL


def test_severity_high_for_large_intensity_noncritical():
    sev = compute_severity(
        intensity=0.9,
        confirmation_successes=2,
        confirmation_trials=3,
        violated_predicates=["sharpe"],
    )
    assert sev == Severity.HIGH


def test_severity_monotone_in_intensity():
    # Holding confirmation + predicate fixed, more intensity => not less severe.
    low = compute_severity(0.1, 3, 3, ["sharpe"])
    high = compute_severity(0.8, 3, 3, ["sharpe"])
    order = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2, Severity.CRITICAL: 3}
    assert order[high] >= order[low]


def test_confirmation_confidence_zero_when_no_trials():
    assert confirmation_confidence(0, 0) == 0.0


def test_confirmation_confidence_full_when_all_confirm():
    # 3/3 confirmations -> high lower-bound confidence (Wilson 95% LCB ~0.29).
    c = confirmation_confidence(3, 3)
    assert 0.2 < c <= 1.0


def test_confirmation_confidence_increases_with_agreement():
    weak = confirmation_confidence(2, 5)
    strong = confirmation_confidence(5, 5)
    assert strong > weak


def test_confirmation_confidence_monotone_in_successes():
    assert confirmation_confidence(3, 5) >= confirmation_confidence(2, 5)
    assert confirmation_confidence(5, 5) >= confirmation_confidence(4, 5)


def test_critical_predicate_membership():
    assert "drawdown" in CRITICAL_PREDICATES
    assert "ruin" in CRITICAL_PREDICATES
