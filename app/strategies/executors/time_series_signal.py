"""Time-series signal executor (reset brief Phase 2 item 20).

Phase 2: single asset, long or cash, simple moving-average crossover.
  fast_sma[t] > slow_sma[t] -> target weight 1 ; else 0.
Requires fast_window < slow_window (both positive); no signal before slow-window
warmup; decision at close, fill next valid open; no shorting; no same-bar;
exactly one tradable asset.
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
from app.strategies.executors._base import tradable_mask
from app.strategies.schedules import decision_mask
from app.strategies.signals import simple_moving_average


class TimeSeriesSignalExecutor:
    strategy_type = StrategyType.TIME_SERIES_SIGNAL

    def _sma_params(self, spec) -> tuple[int, int]:  # noqa: ANN001
        fast, slow = 20, 50
        for sig in spec.signal_definitions:
            if sig.kind == "sma":
                fast, slow = sig.fast_window, sig.slow_window
        return fast, slow

    def validate_spec(self, spec, context: StrategyExecutionContext | None = None) -> list[ValidationIssue]:  # noqa: ANN001
        issues: list[ValidationIssue] = []
        trad_universe = [
            a for a in spec.universe if not (a == spec.benchmark and not spec.benchmark_tradable)
        ]
        if len(trad_universe) != 1:
            issues.append(
                ValidationIssue(
                    code="not_single_asset",
                    message=f"time_series_signal requires exactly one tradable asset, got {trad_universe}",
                    field="universe",
                )
            )
        fast, slow = self._sma_params(spec)
        if not (0 < fast < slow):
            issues.append(
                ValidationIssue(
                    code="bad_sma_windows",
                    message=f"require 0 < fast < slow, got fast={fast} slow={slow}",
                    field="signal_definitions",
                )
            )
        return issues

    def minimum_history_requirements(self, spec) -> HistoryRequirements:  # noqa: ANN001
        from app.strategies.contracts import HistoryRequirements

        slow = self._sma_params(spec)[1]
        return HistoryRequirements(
            min_decision_bars=int(slow) + 1,
            min_execution_bars=int(slow) + 2,
            contiguous_valid_bars=int(slow) + 2,
            signal_reason=f"slow SMA window of {slow} bars plus one execution bar",
            execution_reason="need the warmup bar and the following open to fill",
        )

    def build_targets(self, spec, context: StrategyExecutionContext) -> TargetPlan:  # noqa: ANN001
        close = context.close
        T, N = close.shape
        assets = context.assets
        trad = tradable_mask(spec, assets)
        idxs = [i for i in range(N) if trad[i]]
        if len(idxs) != 1:
            raise ValueError("time_series_signal requires exactly one tradable asset in the panel")
        j = idxs[0]

        fast, slow = self._sma_params(spec)
        col = close[:, j : j + 1]
        fast_sma = simple_moving_average(col, window=fast)[:, 0]
        slow_sma = simple_moving_average(col, window=slow)[:, 0]

        rebal = decision_mask(list(context.dates), spec.frequency.value)
        weights = np.zeros((T, N), dtype=float)
        for t in range(T):
            if not rebal[t]:
                continue
            if not (np.isfinite(fast_sma[t]) and np.isfinite(slow_sma[t])):
                continue  # warmup
            weights[t, j] = 1.0 if fast_sma[t] > slow_sma[t] else 0.0

        return TargetPlan(
            strategy_hash=spec.canonical_hash,
            dates=context.dates,
            assets=assets,
            target_weights=weights,
            rebalance_mask=rebal,
            diagnostics={"asset": assets[j], "fast": fast, "slow": slow},
            warnings=(),
            provenance=dict(context.data_provenance),
        )


__all__ = ["TimeSeriesSignalExecutor"]
