"""Static allocation executor (reset brief Phase 2 item 21).

Actual static allocation: target weights come directly from the approved spec
(e.g. 60/40 SPY/AGG). No signal computation. Monthly rebalance by default; drift
between rebalances is natural; next-open execution; costs on actual trades.
Changing 60/40 -> 70/30 changes hash, targets, trades, and the portfolio path.
"""

from __future__ import annotations

import numpy as np

from app.domain.strategy_spec import StrategyType
from app.strategies.contracts import (
    HistoryRequirements,
    StrategyExecutionContext,
    TargetPlan,
    ValidationIssue,
)
from app.strategies.schedules import decision_mask


class StaticAllocationExecutor:
    strategy_type = StrategyType.STATIC_ALLOCATION

    def validate_spec(self, spec, context: StrategyExecutionContext | None = None) -> list[ValidationIssue]:  # noqa: ANN001
        issues: list[ValidationIssue] = []
        tw = spec.portfolio_construction.target_weights
        if not tw:
            issues.append(
                ValidationIssue(
                    code="missing_target_weights",
                    message="static_allocation requires explicit target_weights",
                    field="portfolio_construction.target_weights",
                )
            )
            return issues
        uni = set(spec.universe)
        for sym in tw:
            if sym not in uni:
                issues.append(
                    ValidationIssue(
                        code="weight_outside_universe",
                        message=f"target weight {sym!r} not in universe",
                        field="portfolio_construction.target_weights",
                    )
                )
        return issues

    def minimum_history_requirements(self, spec) -> HistoryRequirements:  # noqa: ANN001
        return HistoryRequirements(
            min_decision_bars=1,
            min_execution_bars=2,  # decision bar + next valid open
            contiguous_valid_bars=2,
            signal_reason="static allocation has no lookback signal",
            execution_reason="need a decision bar and the following open to fill",
        )

    def build_targets(self, spec, context: StrategyExecutionContext) -> TargetPlan:  # noqa: ANN001
        T, N = context.close.shape
        assets = context.assets
        tw = spec.portfolio_construction.target_weights
        target = np.zeros(N, dtype=float)
        for i, a in enumerate(assets):
            if a in tw:
                target[i] = float(tw[a])

        rebal = decision_mask(list(context.dates), spec.frequency.value)
        weights = np.zeros((T, N), dtype=float)
        for t in range(T):
            if rebal[t]:
                weights[t] = target

        return TargetPlan(
            strategy_hash=spec.canonical_hash,
            dates=context.dates,
            assets=assets,
            target_weights=weights,
            rebalance_mask=rebal,
            diagnostics={"target_weights": {a: float(tw[a]) for a in tw}},
            warnings=(),
            provenance=dict(context.data_provenance),
        )


__all__ = ["StaticAllocationExecutor"]
