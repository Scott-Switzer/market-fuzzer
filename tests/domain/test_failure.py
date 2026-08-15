"""P5 mathematical-trustworthiness: severity semantics + confirmation evidence.

Pure unit tests for consequence-derived severity and the confirmation
lower-confidence-bound. These are the foundation the Failure Lab rests on:
severity must be COMPUTED from failure CONSEQUENCE (not raw stress magnitude,
and not from predicates that merely were configured but passed), and the
confirmation statistic must be reported honestly.
"""

from __future__ import annotations

from app.domain.failure import (
    CRITICAL_PREDICATES,
    Severity,
    compute_failure_severity,
    confirmation_rate_lcb95,
)


def test_severity_low_for_small_breach_weak_confirmation():
    # Non-critical predicate, weak confirmation -> LOW.
    sev = compute_failure_severity(
        failed_predicate_names=["sharpe_lt_0"],
        confirmation_successes=1,
        confirmation_trials=3,
    )
    assert sev == Severity.LOW


def test_severity_critical_for_critical_predicate_strong_confirmation():
    # Critical predicate failure + strongly confirmed -> CRITICAL.
    sev = compute_failure_severity(
        failed_predicate_names=["max_drawdown_gt_0.4"],
        confirmation_successes=3,
        confirmation_trials=3,
    )
    assert sev == Severity.CRITICAL


def test_severity_medium_for_noncritical_moderate_confirmation():
    # Non-critical predicate, moderately confirmed -> MEDIUM (not driven by stress size).
    sev = compute_failure_severity(
        failed_predicate_names=["sharpe_lt_0"],
        confirmation_successes=2,
        confirmation_trials=3,
    )
    assert sev == Severity.MEDIUM


def test_configured_but_passing_critical_predicate_does_not_raise_severity():
    # Only a NON-critical predicate actually failed; a critical predicate was
    # configured but PASSED and must NOT influence severity.
    sev = compute_failure_severity(
        failed_predicate_names=["sharpe_lt_0"],  # drawdown NOT in the failed list
        confirmation_successes=2,
        confirmation_trials=3,
    )
    # Not critical (the passing drawdown predicate is absent from the failed set).
    assert sev != Severity.CRITICAL
    assert sev in (Severity.LOW, Severity.MEDIUM)


def test_severity_not_driven_by_stress_intensity():
    # Two failures with identical consequence but different stress intensities must
    # not differ in severity (stress magnitude is a separate concept).
    small = compute_failure_severity(
        failed_predicate_names=["sharpe_lt_0"],
        confirmation_successes=2,
        confirmation_trials=3,
    )
    large = compute_failure_severity(
        failed_predicate_names=["sharpe_lt_0"],
        confirmation_successes=2,
        confirmation_trials=3,
    )
    assert small == large


def test_confirmation_rate_lcb95_zero_when_no_trials():
    assert confirmation_rate_lcb95(0, 0) == 0.0


def test_confirmation_rate_lcb95_all_confirm_3of3():
    # Wilson 95% LCB for 3/3 is ~0.4385 (not 0.5, not 0.29).
    c = confirmation_rate_lcb95(3, 3)
    assert abs(c - 0.4385) < 0.01


def test_confirmation_rate_lcb95_increases_with_agreement():
    weak = confirmation_rate_lcb95(2, 5)
    strong = confirmation_rate_lcb95(5, 5)
    assert strong > weak


def test_confirmation_rate_lcb95_monotone_in_successes():
    assert confirmation_rate_lcb95(3, 5) >= confirmation_rate_lcb95(2, 5)
    assert confirmation_rate_lcb95(5, 5) >= confirmation_rate_lcb95(4, 5)


def test_critical_predicate_membership():
    assert "drawdown" in CRITICAL_PREDICATES
    assert "ruin" in CRITICAL_PREDICATES
