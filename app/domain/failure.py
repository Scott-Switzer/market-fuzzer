"""Confirmed-failure and minimized-counterexample contracts.

Reset brief sections 8 and 16: a confirmed failure must carry the violated predicate,
seed agreement, severity, and (when minimized) a boundary with a real passing
lower bound. An adjacent pass is never fabricated -- it is either found and
recorded, or honestly absent.

Severity and confirmation evidence are *derived from the evaluation*, never
asserted by default (P5 mathematical-trustworthiness requirement). See
``compute_severity`` and ``confirmation_confidence``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    """Severity semantics (what each level means), derived from evidence.

    LOW       -- threshold-adjacent failure: confirmed by few independent seeds,
                 small intensity shock, non-critical predicate.
    MEDIUM    -- moderate shock OR moderate confirmation on a standard predicate.
    HIGH      -- large shock, confirmed by several independent seeds, or a
                 critical predicate (drawdown / ruin-class).
    CRITICAL  -- large shock AND fully confirmed by independent seeds AND a
                 critical predicate. The strategy fails under a plausible,
                 well-evidenced market stress.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Predicates whose violation is inherently more consequential for a strategy's
# viability (ruin / large-loss class). Used by severity derivation.
CRITICAL_PREDICATES = frozenset({"drawdown", "max_drawdown", "ruin", "margin_call"})


def compute_severity(
    intensity: float,
    confirmation_successes: int,
    confirmation_trials: int,
    violated_predicates: list[str],
) -> Severity:
    """Derive severity from evidence rather than defaulting.

    intensity in [0, 1] (normalized shock magnitude). confirmation_successes /
    confirmation_trials is the independent-seed agreement. A predicate is
    "critical" if any violated predicate name matches ``CRITICAL_PREDICATES``.
    """
    critical = any(
        any(crit in (p.lower() if isinstance(p, str) else "") for crit in CRITICAL_PREDICATES)
        for p in violated_predicates
    )
    agreement = (confirmation_successes / confirmation_trials) if confirmation_trials else 0.0
    strong_confirmation = confirmation_trials >= 3 and agreement >= 0.99

    # Score each axis 0..1 and combine.
    intensity_score = min(1.0, max(0.0, intensity))
    confirm_score = agreement
    score = 0.45 * intensity_score + 0.35 * confirm_score + (0.20 if critical else 0.0)

    if critical and intensity_score >= 0.5 and strong_confirmation:
        return Severity.CRITICAL
    if (
        score >= 0.7
        or (critical and intensity_score >= 0.5)
        or (intensity_score >= 0.7 and confirm_score >= 0.5)
    ):
        return Severity.HIGH
    if score >= 0.4 or (critical or strong_confirmation):
        return Severity.MEDIUM
    return Severity.LOW


def confirmation_confidence(successes: int, trials: int) -> float:
    """Lower-confidence-bound on the failure-confirmation rate.

    Uses the Wilson score interval lower bound at 95% (z=1.96), the standard
    conservative estimate for "how confidently does this fail across independent
    trials". Returns 0.0 when trials == 0. Monotone in successes/trials.
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
    intensity: float
    violated_predicates: list[str]
    seed_agreement: str  # e.g. "2 of 3" -- how many sibling seeds agreed
    severity: Severity = Severity.MEDIUM
    metrics: dict[str, float] = Field(default_factory=dict)
    execution_sensitive: bool = False  # routes to Tier-B exchange replay
    # Confirmation evidence (P5): how the severity/confirmation was established.
    confirmation_trials: int = 0
    confirmation_successes: int = 0
    confidence: float = 0.0


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


__all__ = ["Severity", "ConfirmedFailure", "MinimizedBoundary", "AdjacentPass"]
