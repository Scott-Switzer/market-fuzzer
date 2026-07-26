"""Cross-sectional factor executor (reset brief Phase 2 item 18).

Long/short factor strategy. Phase 2 supports signals: momentum, realized
volatility, and a composite(momentum, low_volatility). Value/quality/fundamental
signals are rejected (no point-in-time data available).

Formulas (exact, tested against hand calculations):
  momentum_12_1[t] = close[t-skip]/close[t-lookback] - 1     (default skip 21, lookback 252)
  realized_vol[t]  = std(daily_returns[t-window+1..t], ddof=1) * sqrt(252)  (default 63)
  composite        = 0.75*(momentum_pct - 0.5) + 0.25*(0.5 - vol_pct)
  selection        = n_long = ceil(long_q * eligible), n_short = ceil(short_q * eligible)
                     (quantile 0 -> count 0); long/short disjoint; sum <= eligible
  exposure         = long_gross=(G+N)/2, short_gross=(G-N)/2 (requires G>=|N|),
                     equal-weight within side, capped at max_position.
"""

from __future__ import annotations

import math

import numpy as np

from app.domain.strategy_spec import StrategySpec, StrategyType
from app.strategies.constraints import equal_weight_capped, split_gross_net
from app.strategies.contracts import (
    HistoryRequirements,
    StrategyExecutionContext,
    TargetPlan,
    ValidationIssue,
)
from app.strategies.executors._base import signal_lookback_bars, tradable_mask
from app.strategies.schedules import decision_mask
from app.strategies.signals import average_rank, momentum_12_1, realized_volatility

_SUPPORTED_SIGNAL_KINDS = {"momentum", "realized_volatility"}


class CrossSectionalFactorExecutor:
    strategy_type = StrategyType.CROSS_SECTIONAL_FACTOR

    def validate_spec(
        self, spec: StrategySpec, context: StrategyExecutionContext | None = None
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for sig in spec.signal_definitions:
            if sig.kind not in _SUPPORTED_SIGNAL_KINDS:
                issues.append(
                    ValidationIssue(
                        code="unsupported_signal",
                        message=f"signal kind '{sig.kind}' not supported by cross_sectional_factor "
                        "(Phase 2: momentum, realized_volatility only)",
                        field="signal_definitions",
                    )
                )
        pc = spec.portfolio_construction
        if pc.long_quantile is None:
            issues.append(
                ValidationIssue(
                    code="missing_long_quantile",
                    message="cross_sectional_factor requires portfolio_construction.long_quantile",
                    field="portfolio_construction.long_quantile",
                )
            )
        rc = spec.risk_constraints
        g = float(rc.gross_exposure_limit)
        net = float(rc.net_exposure_target or 0.0)
        if g + 1e-12 < abs(net):
            issues.append(
                ValidationIssue(
                    code="exposure_infeasible",
                    message=f"gross {g} < |net| {abs(net)}",
                    field="risk_constraints",
                )
            )
        return issues

    def minimum_history_requirements(self, spec: StrategySpec) -> HistoryRequirements:
        warm = signal_lookback_bars(spec)
        return HistoryRequirements(
            min_decision_bars=warm,
            min_execution_bars=warm + 1,
            contiguous_valid_bars=warm + 1,
            signal_reason="cross-sectional momentum/vol lookback + execution bar",
            execution_reason="need the warmup bar and the following open to fill",
        )

    def build_targets(self, spec: StrategySpec, context: StrategyExecutionContext) -> TargetPlan:
        close = context.close
        T, N = close.shape
        assets = context.assets
        trad = tradable_mask(spec, assets)

        # signal parameters
        mom_look, mom_skip, vol_win = 252, 21, 63
        mom_w, vol_w = 0.75, 0.25
        for sig in spec.signal_definitions:
            if sig.kind == "momentum":
                mom_look, mom_skip = sig.lookback, sig.skip
            elif sig.kind == "realized_volatility":
                vol_win = sig.lookback
        if spec.signal_combination:
            mom_w = float(spec.signal_combination.get("momentum", mom_w))
            vol_w = float(spec.signal_combination.get("low_volatility", vol_w))

        use_vol = any(s.kind == "realized_volatility" for s in spec.signal_definitions) or (
            not spec.signal_definitions
        )
        use_mom = any(s.kind == "momentum" for s in spec.signal_definitions) or (not spec.signal_definitions)

        mom = momentum_12_1(close, long=mom_look, short=mom_skip) if use_mom else None
        vol = realized_volatility(close, window=vol_win) if use_vol else None

        pc = spec.portfolio_construction
        rc = spec.risk_constraints
        long_q = float(pc.long_quantile or 0.0)
        short_q = float(pc.short_quantile or 0.0)
        g = float(rc.gross_exposure_limit)
        net = float(rc.net_exposure_target or 0.0)
        max_pos = float(rc.max_position_weight)
        split = split_gross_net(g, net)

        rebal = decision_mask(list(context.dates), spec.frequency.value)
        weights = np.zeros((T, N), dtype=float)
        warnings: list[str] = []

        for t in range(T):
            if not rebal[t]:
                continue
            # eligibility: tradable AND has finite signal(s)
            elig = trad.copy()
            if use_mom and mom is not None:
                elig &= np.isfinite(mom[t])
            if use_vol and vol is not None:
                elig &= np.isfinite(vol[t])
            if elig.sum() < 2:
                continue
            # composite of centered percentile ranks (over eligible only)
            comp = np.full(N, np.nan)
            mom_slice = np.where(elig, mom[t], np.nan) if (use_mom and mom is not None) else None
            vol_slice = np.where(elig, vol[t], np.nan) if (use_vol and vol is not None) else None
            mom_rank = average_rank(mom_slice) if mom_slice is not None else None
            vol_rank = average_rank(vol_slice) if vol_slice is not None else None
            comp_vals = np.zeros(N, dtype=float)
            if mom_rank is not None:
                comp_vals += mom_w * (np.nan_to_num(mom_rank) - 0.5)
            if vol_rank is not None:
                comp_vals += vol_w * (0.5 - np.nan_to_num(vol_rank))
            comp = np.where(elig, comp_vals, np.nan)

            eligible_count = int(elig.sum())
            n_long = 0 if long_q == 0 else int(math.ceil(long_q * eligible_count))
            n_short = 0 if short_q == 0 else int(math.ceil(short_q * eligible_count))
            if n_long + n_short > eligible_count:
                # shrink proportionally, keep disjoint
                n_short = max(0, eligible_count - n_long)
            # rank eligible by composite (descending) with deterministic tie order
            order = _stable_desc_order(comp)
            longs = np.zeros(N, dtype=bool)
            shorts = np.zeros(N, dtype=bool)
            for k in range(n_long):
                longs[order[k]] = True
            for k in range(n_short):
                shorts[order[-(k + 1)]] = True
            # ensure disjoint (guaranteed by n_long+n_short<=eligible_count)
            lw, lfeas = equal_weight_capped(longs, split.long_gross, max_pos)
            sw, sfeas = equal_weight_capped(shorts, split.short_gross, max_pos)
            w = lw - sw
            if not (lfeas and sfeas):
                warnings.append(f"exposure_shortfall at t={t}: capped by max_position")
            weights[t] = w

        return TargetPlan(
            strategy_hash=spec.canonical_hash,
            dates=context.dates,
            assets=assets,
            target_weights=weights,
            rebalance_mask=rebal,
            diagnostics={"n_bars": T, "long_quantile": long_q, "short_quantile": short_q},
            warnings=tuple(dict.fromkeys(warnings)),
            provenance=dict(context.data_provenance),
        )


def _stable_desc_order(comp: np.ndarray) -> np.ndarray:
    """Indices sorted by composite descending; NaN last; ties by index asc."""
    n = comp.shape[0]
    keys = [(-(comp[i]) if np.isfinite(comp[i]) else math.inf, i) for i in range(n)]
    keys.sort()
    return np.array([i for _, i in keys], dtype=int)


__all__ = ["CrossSectionalFactorExecutor"]
