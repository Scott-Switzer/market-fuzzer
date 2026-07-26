"""Tactical allocation executor (reset brief Phase 2 item 22).

One limited, explicit Phase 2 tactical strategy:
  universe: asset-class ETFs
  lookback: 252 bars (total return)
  trend window: 200 bars (close > 200-day SMA eligibility)
  selection: top K by 252-day return among trend-passing names
  weighting: equal among selected
  fallback: explicit defensive symbol (100% when no risky asset passes the filter)
  frequency: monthly ; execution: next open

The fallback symbol must be declared and present in data; it is disclosed in the
clause ledger. This is a narrow supported implementation, not arbitrary tactical.
"""

from __future__ import annotations

import numpy as np

from app.domain.strategy_spec import StrategyType
from app.strategies.contracts import StrategyExecutionContext, TargetPlan, ValidationIssue
from app.strategies.executors._base import tradable_mask
from app.strategies.executors.cross_sectional_factor import _stable_desc_order
from app.strategies.schedules import decision_mask
from app.strategies.signals import simple_moving_average, total_return


class TacticalAllocationExecutor:
    strategy_type = StrategyType.TACTICAL_ALLOCATION

    def _params(self, spec) -> tuple[int, int, int, str | None]:  # noqa: ANN001
        lookback = int(spec.lookback_windows.get("return", 252))
        trend = int(spec.lookback_windows.get("trend", 200))
        top_k = int(spec.ranking_or_threshold_rules.get("top_k", 3))
        fallback = spec.ranking_or_threshold_rules.get("fallback")
        return lookback, trend, top_k, fallback

    def validate_spec(self, spec, context: StrategyExecutionContext | None = None) -> list[ValidationIssue]:  # noqa: ANN001
        issues: list[ValidationIssue] = []
        _, _, top_k, fallback = self._params(spec)
        if not fallback:
            issues.append(
                ValidationIssue(
                    code="missing_fallback",
                    message="tactical_allocation requires a declared fallback symbol "
                    "(ranking_or_threshold_rules.fallback)",
                    field="ranking_or_threshold_rules.fallback",
                )
            )
        elif fallback not in spec.universe:
            issues.append(
                ValidationIssue(
                    code="fallback_not_in_universe",
                    message=f"fallback {fallback!r} must be in the universe",
                    field="universe",
                )
            )
        if top_k < 1:
            issues.append(
                ValidationIssue(
                    code="bad_top_k", message="top_k must be >= 1", field="ranking_or_threshold_rules"
                )
            )
        if context is not None and fallback and fallback not in context.assets:
            issues.append(
                ValidationIssue(
                    code="fallback_missing_data",
                    message=f"fallback {fallback!r} not present in market data",
                    field="universe",
                )
            )
        return issues

    def build_targets(self, spec, context: StrategyExecutionContext) -> TargetPlan:  # noqa: ANN001
        close = context.close
        T, N = close.shape
        assets = context.assets
        lookback, trend, top_k, fallback = self._params(spec)
        trad = tradable_mask(spec, assets)
        # fallback index
        fb_idx = assets.index(fallback) if fallback in assets else None

        ret = total_return(close, lookback=lookback)
        sma = simple_moving_average(close, window=trend)

        rebal = decision_mask(list(context.dates), spec.frequency.value)
        weights = np.zeros((T, N), dtype=float)
        warnings: list[str] = []

        risky = trad.copy()
        if fb_idx is not None:
            risky[fb_idx] = False  # fallback is defensive, not a risky pick

        for t in range(T):
            if not rebal[t]:
                continue
            elig = risky & np.isfinite(ret[t]) & np.isfinite(sma[t]) & (close[t] > sma[t])
            ec = int(elig.sum())
            if ec == 0:
                if fb_idx is not None:
                    weights[t, fb_idx] = 1.0
                else:
                    warnings.append(f"no fallback available at t={t}")
                continue
            score = np.where(elig, ret[t], np.nan)
            order = _stable_desc_order(score)
            k = min(top_k, ec)
            for r in range(k):
                weights[t, order[r]] = 1.0 / k

        return TargetPlan(
            strategy_hash=spec.canonical_hash,
            dates=context.dates,
            assets=assets,
            target_weights=weights,
            rebalance_mask=rebal,
            diagnostics={"lookback": lookback, "trend": trend, "top_k": top_k, "fallback": fallback},
            warnings=tuple(dict.fromkeys(warnings)),
            provenance=dict(context.data_provenance),
        )


__all__ = ["TacticalAllocationExecutor"]
