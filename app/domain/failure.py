"""Confirmed-failure and minimized-counterexample contracts.

Reset brief sections 8 and 16: a confirmed failure must carry the violated predicate,
seed agreement, severity, and (when minimized) a boundary with a real passing
lower bound. An adjacent pass is never fabricated -- it is either found and
recorded, or honestly absent.

P5 mathematical-trustworthiness: we deliberately separate two concepts that the
original model collapsed into one number:

* ``stress_intensity`` -- the RAW magnitude of the generated market perturbation.
  This is preserved directly; it describes the *scenario*, not the strategy.
* ``failure_severity`` -- the CONSEQUENCE of the observed failure, derived from
  what predicate actually failed and the independent-confirmation evidence. It
  does NOT increase merely because the required shock was larger. A strategy
  that fails at a *small* intensity is *more fragile*; fragility is captured by
  ``boundary_distance`` (the size of the minimized failing perturbation),
  tracked by minimization -- not here.

Confirmed-failure evidence fields (``severity``, ``stress_intensity``,
``confirmation_trials/successes/rate/lcb95``) are REQUIRED -- there is no silent
default. A confirmed failure without derived evidence cannot be constructed, so
the "medium-severity, zero-evidence" anti-pattern this PR eliminates is
structurally impossible. See ``compute_failure_severity`` and
``confirmation_rate_lcb95``.

NOTE on breach magnitude: a generic dimensionless ``abs(value - threshold) /
abs(threshold)`` score is NOT used to drive categorical severity. Sharpe,
cumulative return, drawdown, and turnover are not commensurable under one
normalization, and the threshold-zero case is arbitrary. Raw per-predicate
breach (observed value vs threshold) is retained in the evaluation record, but
metric-specific calibration (option B) is deferred; severity here is driven by
predicate criticality + confirmation evidence only.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Severity(StrEnum):
    """Severity semantics (what each level means), derived from failure CONSEQUENCE.

    Note: severity reflects the consequence of the observed failure, NOT the raw
    stress magnitude. A strategy failing at a small intensity is more fragile
    than one failing only at a large intensity; fragility lives in the
    minimization boundary, not in ``Severity``.

    LOW       -- failure on a non-critical predicate, weakly confirmed.
    MEDIUM    -- a critical-predicate failure, or moderate independent confirmation.
    HIGH      -- strongly confirmed across independent seeds.
    CRITICAL  -- critical-predicate (drawdown / ruin class) failure that is
                 strongly confirmed by independent seeds.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Predicates whose violation is inherently more consequential for a strategy's
# viability (ruin / large-loss class). Used by severity derivation. Matched by
# substring against the *failed* predicate description only.
CRITICAL_PREDICATES = frozenset({"drawdown", "ruin", "margin_call"})


def _is_critical(failed_predicate_names: list[str]) -> bool:
    return any(any(crit in name.lower() for crit in CRITICAL_PREDICATES) for name in failed_predicate_names)


def compute_failure_severity(
    failed_predicate_names: list[str],
    confirmation_successes: int,
    confirmation_trials: int,
) -> Severity:
    """Derive failure severity from CONSEQUENCE, not stress magnitude.

    ``failed_predicate_names`` -- ONLY the predicates that actually failed
    (not every configured predicate). ``confirmation_*`` is the
    independent-seed agreement. Raw ``stress_intensity`` is intentionally NOT
    an input -- it is recorded separately. Breach magnitude is NOT folded into
    categorical severity (metrics are not cross-commensurable under one
    normalization); metric-specific calibration is deferred.
    """
    critical = _is_critical(failed_predicate_names)
    agreement = (confirmation_successes / confirmation_trials) if confirmation_trials else 0.0
    strong_confirmation = confirmation_trials >= 3 and agreement >= 0.99

    if critical and strong_confirmation:
        return Severity.CRITICAL
    if strong_confirmation:
        return Severity.HIGH
    if critical or agreement >= 0.5:
        return Severity.MEDIUM
    return Severity.LOW


def confirmation_rate_lcb95(successes: int, trials: int) -> float:
    """95% Wilson lower confidence bound on the repeated-failure rate.

    This is the LOWER bound of a confidence interval for the *observed failure
    rate across independent trials* -- it is NOT a probability that the failure
    is "real". Returns 0.0 when trials == 0. Monotone in successes/trials.
    """
    if trials <= 0:
        return 0.0
    p = successes / trials
    z = 1.96
    denom = 1 + z * z / trials
    center = (p + z * z / (2 * trials)) / denom
    margin = (z * ((p * (1 - p) + z * z / (4 * trials)) / trials) ** 0.5) / denom
    return max(0.0, center - margin)


class ConfirmedFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failure_id: str
    strategy_hash: str
    world_hash: str
    mechanism: str
    stress_intensity: float  # raw scenario magnitude; NOT an input to severity
    violated_predicates: list[str]
    seed_agreement: str  # e.g. "2 of 3" -- how many sibling seeds agreed
    # --- evidence is REQUIRED; no silent defaults (anti-pattern eliminated) ---
    severity: Severity
    confirmation_trials: int
    confirmation_successes: int
    confirmation_rate: float
    confirmation_rate_lcb95: float
    metrics: dict[str, float] = Field(default_factory=dict)
    execution_sensitive: bool = False  # routes to Tier-B exchange replay

    @model_validator(mode="after")
    def _check_evidence(self) -> ConfirmedFailure:
        assert self.confirmation_trials > 0, "confirmation_trials must be > 0"
        assert 0 <= self.confirmation_successes <= self.confirmation_trials, (
            "successes must be in [0, trials]"
        )
        expected_rate = self.confirmation_successes / self.confirmation_trials
        assert abs(self.confirmation_rate - expected_rate) < 1e-9, (
            "confirmation_rate must equal successes/trials"
        )
        assert 0.0 <= self.confirmation_rate_lcb95 <= self.confirmation_rate + 1e-9, (
            "lcb95 must be in [0, rate]"
        )
        assert 0.0 <= self.confirmation_rate <= 1.0
        return self


class MinimizedBoundary(BaseModel):
    """Result of minimization. Invariant: when ``still_fails`` is True, the
    minimized value must be strictly beyond ``passing_lower_bound`` (integrity gate 14)."""

    model_config = ConfigDict(extra="forbid")

    parameter: str
    minimized_value: float
    passing_lower_bound: float
    still_fails: bool
    violated_predicates: list[str] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)

    def check_invariant(self) -> bool:
        if self.still_fails:
            return self.minimized_value > self.passing_lower_bound
        return True


class AdjacentPass(BaseModel):
    """A nearest passing variant. ``found`` MUST be False when none exists --
    fabricating one is integrity gate 15."""

    model_config = ConfigDict(extra="forbid")

    found: bool
    parameter: str | None = None
    value: float | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    diff_from_failure: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "Severity",
    "CRITICAL_PREDICATES",
    "compute_failure_severity",
    "confirmation_rate_lcb95",
    "ConfirmedFailure",
    "MinimizedBoundary",
    "AdjacentPass",
]
