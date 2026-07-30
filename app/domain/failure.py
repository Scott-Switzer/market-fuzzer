"""Confirmed-failure and minimized-counterexample contracts.

Reset brief sections 8 and 16: a confirmed failure must carry the violated predicate,
seed agreement, severity, and (when minimized) a boundary with a real passing
lower bound. An adjacent pass is never fabricated -- it is either found and
recorded, or honestly absent.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


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
