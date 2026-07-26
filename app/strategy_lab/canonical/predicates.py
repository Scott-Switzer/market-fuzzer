"""Typed failure-predicate evaluation (Phase 2.6 section 9).

Free-form predicate strings are replaced by structured ``FailurePredicate``
records. Unknown metrics / operators, NaN/Infinity thresholds, empty predicate
lists, and contradictory duplicate predicates are all rejected (never silently
return False). Every evaluation records the actual metric value, the operator,
the threshold, and the Boolean result.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from app.strategy_lab.canonical.contracts import PredicateResult


class MetricName(StrEnum):
    SHARPE = "sharpe"
    CUMULATIVE_RETURN = "cumulative_return"
    MAX_DRAWDOWN = "max_drawdown"
    TURNOVER = "turnover"


class ComparisonOperator(StrEnum):
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"


@dataclass(frozen=True)
class FailurePredicate:
    metric: MetricName
    operator: ComparisonOperator
    threshold: Decimal


def parse_predicate(raw: str) -> FailurePredicate:
    """Parse a legacy free-form predicate string (e.g. ``sharpe_below_0``) into a
    typed ``FailurePredicate``. Raises ``ValueError`` for unknown predicates."""
    try:
        metric, op, thr = raw.rsplit("_", 2)
    except ValueError as exc:
        raise ValueError(f"unparseable predicate: {raw!r}") from exc
    if metric == "sharpe":
        m = MetricName.SHARPE
    elif metric in ("negative", "return"):
        # legacy aliases: negative_return, cumulative_return
        m = MetricName.CUMULATIVE_RETURN
    elif metric == "max":
        m = MetricName.MAX_DRAWDOWN
    elif metric == "cumulative":
        m = MetricName.CUMULATIVE_RETURN
    else:
        raise ValueError(f"unknown predicate metric: {metric!r} in {raw!r}")
    if op != "below":
        raise ValueError(f"unsupported predicate operator: {op!r} in {raw!r}")
    try:
        thr_d = Decimal(thr)
    except InvalidOperation as exc:
        raise ValueError(f"invalid predicate threshold: {thr!r}") from exc
    if not thr_d.is_finite():
        raise ValueError(f"non-finite predicate threshold: {thr!r}")
    return FailurePredicate(metric=m, operator=ComparisonOperator.LT, threshold=thr_d)


def parse_predicates(raw_predicates: list[str]) -> list[FailurePredicate]:
    """Parse + validate a request's predicate list. Rejects empties and
    contradictory duplicates (same metric+operator with different thresholds)."""
    if not raw_predicates:
        raise ValueError("empty predicate list is not allowed")
    out: list[FailurePredicate] = []
    seen: dict[tuple[str, str], Decimal] = {}
    for raw in raw_predicates:
        p = parse_predicate(raw)
        key = (p.metric.value, p.operator.value)
        if key in seen and seen[key] != p.threshold:
            raise ValueError(
                f"contradictory duplicate predicate for {key}: {seen[key]} vs {p.threshold}"
            )
        seen[key] = p.threshold
        out.append(p)
    return out


def _metric_value(metric: MetricName, metrics: dict[str, Any]) -> Decimal:
    raw = metrics.get(metric.value)
    if raw is None:
        raise ValueError(f"metric {metric.value} not present in result")
    try:
        v = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError(f"non-numeric metric {metric.value}: {raw!r}") from exc
    if not v.is_finite():
        raise ValueError(f"non-finite metric {metric.value}: {raw!r}")
    return v


def evaluate_predicate(pred: FailurePredicate, metrics: dict[str, Any]) -> PredicateResult:
    value = _metric_value(pred.metric, metrics)
    if pred.operator == ComparisonOperator.LT:
        failed = value < pred.threshold
    elif pred.operator == ComparisonOperator.LTE:
        failed = value <= pred.threshold
    elif pred.operator == ComparisonOperator.GT:
        failed = value > pred.threshold
    elif pred.operator == ComparisonOperator.GTE:
        failed = value >= pred.threshold
    else:  # pragma: no cover - guarded by parse_predicates
        raise ValueError(f"unknown operator {pred.operator}")
    return PredicateResult(
        metric=pred.metric.value,
        operator=pred.operator.value,
        threshold=pred.threshold,
        value=value,
        passed=not failed,
        failed=failed,
    )


def evaluate_predicates(
    predicates: list[FailurePredicate], metrics: dict[str, Any]
) -> list[PredicateResult]:
    return [evaluate_predicate(p, metrics) for p in predicates]


def predicates_failed(predicate_results: list[PredicateResult]) -> bool:
    """True iff ANY failure predicate is satisfied (the strategy failed)."""
    return any(pr.failed for pr in predicate_results)


__all__ = [
    "MetricName",
    "ComparisonOperator",
    "FailurePredicate",
    "parse_predicate",
    "parse_predicates",
    "evaluate_predicate",
    "evaluate_predicates",
    "predicates_failed",
]
