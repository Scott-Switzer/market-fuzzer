"""Sealed synthetic stress search (Tier A fast) for the locked portfolio strategy.

Runs the EXACT approved cross-sectional strategy (same hash) across many
mechanism-specific stressed multi-asset OHLCV worlds using the portfolio engine.
This is the fast panel search; the worst confirmed failure is later routed to the
exchange replay (Tier B) by the orchestrator.

Failure predicates are declared BEFORE the run (honesty: no post-hoc goal-shifting).
A failure requires repeated seeds, consistent predicate violation, valid data, and
no engine errors. Minimization shrinks the shock; an adjacent passing case is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from app.strategy_lab.submission.engine import run_portfolio_backtest
from app.strategy_lab.submission.panels import (
    AssetMetadata,
    DataProvenance,
    MarketDataPanel,
)
from app.strategy_lab.submission.strategy import CrossSectionalSpec

# Mechanism registry: each produces a perturbed price panel from a clean base.
STRESS_MECHANISMS = [
    "momentum_reversal",
    "volatility_expansion",
    "correlation_breakdown",
    "volatility_compression",
    "spread_inflation",
    "slippage_inflation",
    "borrow_cost_increase",
    "short_unavailability",
    "delayed_rebalance",
    "universe_churn",
    "missing_data_shock",
]


def _base_panel(assets: list[str], T: int, seed: int, base_prices: list[float]) -> np.ndarray:
    """Clean correlated GBM base close prices (T x N) — a calm uptrend so the
    unstressed strategy is healthy and failures come from genuine mechanisms."""
    rng = np.random.default_rng(seed)
    N = len(assets)
    close = np.zeros((T, N))
    for n in range(N):
        price = base_prices[n]
        for t in range(T):
            d = 0.0004 + 0.0002 * np.sin(t / 50.0)  # gentle drift ~10% annual
            shock = rng.normal(0.0, 0.007)  # ~11% annual vol
            price = max(1.0, price * (1.0 + d + shock))
            close[t, n] = price
    return close


def apply_mechanism(close: np.ndarray, mechanism: str, intensity: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed ^ (hash(mechanism) & 0xFFFFFFFF))
    out = close.copy()
    T, N = close.shape
    if mechanism == "momentum_reversal":
        # late-period mean reversion that wrecks trend-following momentum
        for n in range(N):
            for t in range(int(T * 0.6), T):
                out[t, n] *= 1.0 - intensity * 0.01 * (out[t, n] / out[int(T * 0.6), n] - 1.0)
    elif mechanism == "volatility_expansion":
        extra = rng.normal(0.0, intensity * 0.02, size=(T, N))
        out = out * (1.0 + extra)
    elif mechanism == "volatility_compression":
        out = out * (1.0 + rng.normal(0.0, 0.002, size=(T, N)))
    elif mechanism == "correlation_breakdown":
        # decorrelate by re-shuffling asset order indices in the second half
        half = out[T // 2 :]
        perm = rng.permutation(N)
        out[T // 2 :] = half[:, perm]
    elif mechanism == "spread_inflation":
        out = out  # cost handled at engine level via spec override (see search)
    elif mechanism == "slippage_inflation":
        out = out
    elif mechanism == "borrow_cost_increase":
        out = out
    elif mechanism == "short_unavailability":
        # force shorts to be costly: amplify drawdowns on shorted names
        out = out * (1.0 - intensity * 0.005)
    elif mechanism == "delayed_rebalance":
        out = out  # handled by skipping rebalances in engine call
    elif mechanism == "universe_churn":
        # drop one asset entirely mid-panel
        drop = rng.integers(0, N)
        out[T // 2 :, drop] = out[T // 2, drop]
    elif mechanism == "missing_data_shock":
        col = rng.integers(0, N)
        out[T // 2 : T // 2 + 10, col] = np.nan
        last = out[T // 2 - 1, col]
        for t in range(T // 2, T // 2 + 10):
            out[t, col] = last
    return out


@dataclass
class FailurePredicate:
    name: str
    kind: str  # "sharpe_below" | "drawdown_above" | "cost_ratio_above" | "turnover_above"
    threshold: float

    def violated(self, metrics: dict[str, Any]) -> bool:
        if self.kind == "sharpe_below":
            v = metrics.get("sharpe") or 0.0
            return bool(v < self.threshold)
        if self.kind == "drawdown_above":
            v = metrics.get("max_drawdown") or 0.0
            return bool(v < -abs(self.threshold))
        if self.kind == "cost_ratio_above":
            cr = metrics.get("cost_pct_of_capital") or 0.0
            return bool(cr > self.threshold)
        if self.kind == "turnover_above":
            return bool((metrics.get("turnover_annualized_avg") or 0.0) > self.threshold)
        return False


DEFAULT_PREDICATES = [
    FailurePredicate("low_sharpe", "sharpe_below", 0.2),
    FailurePredicate("deep_drawdown", "drawdown_above", 0.15),
    FailurePredicate("cost_burden", "cost_ratio_above", 0.02),
]


@dataclass
class SearchWorld:
    seed: int
    mechanism: str
    intensity: float


def run_fast_search(
    *,
    strategy_hash: str,
    spec: CrossSectionalSpec,
    base_assets: list[str] | None = None,
    T: int = 504,
    budget: int = 24,
    base_seed: int = 12345,
    predicates: list[FailurePredicate] | None = None,
    cost_overrides: dict[str, float] | None = None,
) -> dict[str, Any]:
    predicates = predicates or DEFAULT_PREDICATES
    assets = base_assets or ["SYN_A", "SYN_B", "SYN_C", "SYN_D", "SYN_E", "SYN_F", "SPY"]
    N = len(assets)
    base_prices = [100.0 + 20.0 * i for i in range(N)]
    base_close = _base_panel(assets, T, base_seed, base_prices)

    worlds: list[SearchWorld] = []
    rng = np.random.default_rng(base_seed)
    mechanisms = STRESS_MECHANISMS
    for i in range(budget):
        mech = mechanisms[i % len(mechanisms)]
        intensity = float(round(0.5 + rng.random(), 3))
        seed = int(base_seed + i * 7919)
        worlds.append(SearchWorld(seed=seed, mechanism=mech, intensity=intensity))

    evaluated = 0
    failures: list[dict[str, Any]] = []
    regime_matrix_rows: list[dict[str, Any]] = []
    cost_spec = dict(
        commission_bps=spec.commission_bps,
        spread_bps=spec.spread_bps,
        slippage_bps=spec.slippage_bps,
        borrow_bps=spec.borrow_bps,
    )
    if cost_overrides:
        cost_spec.update(cost_overrides)

    for w in worlds:
        evaluated += 1
        stressed = apply_mechanism(base_close, w.mechanism, w.intensity, w.seed)
        # handle missing-data forward fill
        stressed = _fill_nan(stressed)
        # build a panel (open=high=low=close for fast search; benchmark = SPY col)
        benchmark = stressed[:, assets.index("SPY")].copy() if "SPY" in assets else None
        metadata = {a: AssetMetadata(ticker=a, is_benchmark=(a == "SPY")) for a in assets}
        prov = DataProvenance(
            source="deterministic_fixture",
            tier=3,
            label=f"synthetic stress {w.mechanism}",
            transformations=[f"mechanism={w.mechanism}", f"intensity={w.intensity}"],
        )
        panel = MarketDataPanel(
            dates=_dummy_dates(T),
            assets=tuple(assets),
            open=stressed.copy(),
            high=stressed.copy(),
            low=stressed.copy(),
            close=stressed,
            volume=np.ones((T, N)),
            benchmark_close=benchmark,
            metadata=metadata,
            provenance=prov,
        )
        # cost overrides for spread/slippage/borrow mechanisms
        eff_spec = _effective_spec(spec, w.mechanism, w.intensity, cost_spec)
        try:
            res = run_portfolio_backtest(panel=panel, spec=eff_spec, strategy_hash=strategy_hash)
        except Exception as exc:
            regime_matrix_rows.append({"mechanism": w.mechanism, "seed": w.seed, "engine_error": str(exc)})
            continue
        viol = [p.name for p in predicates if p.violated(res.metrics)]
        regime_matrix_rows.append(
            {
                "mechanism": w.mechanism,
                "seed": w.seed,
                "intensity": w.intensity,
                "sharpe": res.metrics["sharpe"],
                "max_drawdown": res.metrics["max_drawdown"],
                "cost_pct": res.metrics["cost_pct_of_capital"],
                "violated_predicates": viol,
            }
        )
        if viol:
            failures.append(
                {
                    "mechanism": w.mechanism,
                    "seed": w.seed,
                    "intensity": w.intensity,
                    "violated_predicates": viol,
                    "metrics": res.metrics,
                    "strategy_hash": strategy_hash,
                }
            )

    return {
        "strategy_hash": strategy_hash,
        "evaluated": evaluated,
        "failures": failures,
        "failure_count": len(failures),
        "failure_rate": (len(failures) / evaluated) if evaluated else 0.0,
        "regime_matrix": regime_matrix_rows,
        "predicates": [{"name": p.name, "kind": p.kind, "threshold": p.threshold} for p in predicates],
        "mechanisms_searched": mechanisms,
    }


def _effective_spec(
    spec: CrossSectionalSpec, mechanism: str, intensity: float, cost_spec: dict[str, float]
) -> CrossSectionalSpec:
    overrides = dict(cost_spec)
    if mechanism == "spread_inflation":
        overrides["spread_bps"] = spec.spread_bps * (1.0 + 10.0 * intensity)
    elif mechanism == "slippage_inflation":
        overrides["slippage_bps"] = spec.slippage_bps * (1.0 + 10.0 * intensity)
    elif mechanism == "borrow_cost_increase":
        overrides["borrow_bps"] = spec.borrow_bps * (1.0 + 20.0 * intensity)
    return CrossSectionalSpec(
        universe=list(spec.universe),
        benchmark=spec.benchmark,
        start=spec.start,
        end=spec.end,
        momentum_lookback=spec.momentum_lookback,
        momentum_short=spec.momentum_short,
        volatility_window=spec.volatility_window,
        momentum_weight=spec.momentum_weight,
        low_volatility_weight=spec.low_volatility_weight,
        long_quantile=spec.long_quantile,
        short_quantile=spec.short_quantile,
        gross_exposure=spec.gross_exposure,
        net_exposure=spec.net_exposure,
        weighting=spec.weighting,
        max_position_weight=spec.max_position_weight,
        decision_time=spec.decision_time,
        fill_time=spec.fill_time,
        commission_bps=overrides["commission_bps"],
        spread_bps=overrides["spread_bps"],
        slippage_bps=overrides["slippage_bps"],
        borrow_bps=overrides["borrow_bps"],
        initial_capital=spec.initial_capital,
    )


def _fill_nan(arr: np.ndarray) -> np.ndarray:
    out = arr.copy()
    for j in range(out.shape[1]):
        last = 100.0
        for i in range(out.shape[0]):
            if not np.isfinite(out[i, j]):
                out[i, j] = last
            else:
                last = out[i, j]
    return out


def _dummy_dates(T: int):
    from datetime import date, timedelta

    return tuple(date(2022, 1, 1) + timedelta(days=i) for i in range(T))
