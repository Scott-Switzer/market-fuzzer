from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FailureCategory(StrEnum):
    TREND_REVERSAL = "trend_reversal"
    VOLATILITY_EXPANSION = "volatility_expansion"
    CORRELATION_BREAKDOWN = "correlation_breakdown"
    LIQUIDITY_DETERIORATION = "liquidity_deterioration"
    TRANSACTION_COST_INFLATION = "transaction_cost_inflation"
    PARAMETER_INSTABILITY = "parameter_instability"
    OVERFITTING = "overfitting"


FailureSeverity = StrEnum(
    "FailureSeverity", {"LOW": "low", "MEDIUM": "medium", "HIGH": "high", "CRITICAL": "critical"}
)  # type: ignore[misc]


@dataclass(frozen=True)
class ThresholdPredicate:
    metric: str
    comparator: str
    threshold: float
    weight: float = 1.0


@dataclass(frozen=True)
class FailureEvidence:
    observed: float
    threshold: float
    comparator: str
    metric: str


@dataclass(frozen=True)
class FailureRecord:
    category: FailureCategory
    severity: FailureSeverity
    evidence: FailureEvidence
    candidate: dict[str, Any]
    minimized_candidate: dict[str, Any] | None = None
    replay_artifact_id: str | None = None
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


def _total_return_criterion(metrics: dict[str, Any], threshold: float) -> bool:
    try:
        return float(metrics.get("total_return_pct", 0.0)) <= threshold
    except Exception:
        return False


def _max_drawdown_criterion(metrics: dict[str, Any], threshold: float) -> bool:
    try:
        return float(metrics.get("max_drawdown_pct", 0.0)) <= threshold
    except Exception:
        return False


def _sharpe_criterion(metrics: dict[str, Any], threshold: float) -> bool:
    try:
        return float(metrics.get("sharpe", 0.0)) <= threshold
    except Exception:
        return False


PREDICATE_REGISTRY: dict[str, Callable[[dict[str, Any], float], bool]] = {
    "total_return_pct_le": _total_return_criterion,
    "max_drawdown_pct_le": _max_drawdown_criterion,
    "sharpe_le": _sharpe_criterion,
}


def build_predicates(specs: Sequence[ThresholdPredicate]) -> list[Callable[[dict[str, Any]], bool]]:
    out: list[Callable[[dict[str, Any]], bool]] = []
    for spec in specs:
        fn = PREDICATE_REGISTRY.get(f"{spec.metric}_{spec.comparator}")
        if fn is None:
            raise KeyError(f"Unsupported predicate {spec.metric}_{spec.comparator}")
        out.append(lambda metrics, _fn=fn, _th=spec.threshold: _fn(metrics, _th))  # type: ignore[misc]
    return out


def evaluate_predicates(
    metrics: dict[str, Any], predicates: Sequence[Callable[[dict[str, Any]], bool]]
) -> list[bool]:
    return [fn(metrics) for fn in predicates]
