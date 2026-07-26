"""Long-only ranking executor (reset brief Phase 2 item 19).

Signal = momentum; selection = top_n or top_quantile; weighting = equal.
No shorts; gross = declared long gross when feasible; position cap enforced;
benchmark excluded unless explicitly tradable; shared scheduler.
"""

from __future__ import annotations

import math

import numpy as np

from app.domain.strategy_spec import StrategySpec, StrategyType
from app.strategies.constraints import equal_weight_capped
from app.strategies.contracts import StrategyExecutionContext, TargetPlan, ValidationIssue
from app.strategies.executors._base import tradable_mask
from app.strategies.executors.cross_sectional_factor import _stable_desc_order
from app.strategies.schedules import decision_mask
from app.strategies.signals import momentum_12_1


class LongOnlyRankingExecutor:
    strategy_type = StrategyType.LONG_ONLY_RANKING

    def validate_spec(
        self, spec: StrategySpec, context: StrategyExecutionContext | None = None
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        pc = spec.portfolio_construction
        if pc.long_count is None and pc.long_quantile is None:
            issues.append(
                ValidationIssue(
                    code="missing_selection",
                    message="long_only_ranking requires long_count or long_quantile",
                    field="portfolio_construction",
                )
            )
        if pc.short_count or pc.short_quantile:
            issues.append(
                ValidationIssue(
                    code="unexpected_short",
                    message="long_only_ranking cannot take short positions",
                    field="portfolio_construction",
                )
            )
        return issues

    def build_targets(self, spec: StrategySpec, context: StrategyExecutionContext) -> TargetPlan:
        close = context.close
        T, N = close.shape
        assets = context.assets
        trad = tradable_mask(spec, assets)

        mom_look, mom_skip = 252, 21
        for sig in spec.signal_definitions:
            if sig.kind == "momentum":
                mom_look, mom_skip = sig.lookback, sig.skip
        mom = momentum_12_1(close, long=mom_look, short=mom_skip)

        pc = spec.portfolio_construction
        rc = spec.risk_constraints
        g = float(rc.gross_exposure_limit)
        max_pos = float(rc.max_position_weight)
        long_count = pc.long_count
        long_q = float(pc.long_quantile) if pc.long_quantile is not None else None

        rebal = decision_mask(list(context.dates), spec.frequency.value)
        weights = np.zeros((T, N), dtype=float)
        warnings: list[str] = []

        for t in range(T):
            if not rebal[t]:
                continue
            elig = trad & np.isfinite(mom[t])
            ec = int(elig.sum())
            if ec < 1:
                continue
            if long_count is not None:
                n_long = min(long_count, ec)
            else:
                n_long = 0 if long_q == 0 else min(ec, int(math.ceil((long_q or 0.0) * ec)))
            if n_long < 1:
                continue
            score = np.where(elig, mom[t], np.nan)
            order = _stable_desc_order(score)
            longs = np.zeros(N, dtype=bool)
            for k in range(n_long):
                longs[order[k]] = True
            w, feas = equal_weight_capped(longs, g, max_pos)
            if not feas:
                warnings.append(f"exposure_shortfall at t={t}: long gross capped by max_position")
            weights[t] = w

        return TargetPlan(
            strategy_hash=spec.canonical_hash,
            dates=context.dates,
            assets=assets,
            target_weights=weights,
            rebalance_mask=rebal,
            diagnostics={"n_bars": T, "long_count": long_count, "long_quantile": long_q},
            warnings=tuple(dict.fromkeys(warnings)),
            provenance=dict(context.data_provenance),
        )


__all__ = ["LongOnlyRankingExecutor"]
