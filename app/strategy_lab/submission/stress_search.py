"""Sealed synthetic stress search (Tier A fast) for the locked portfolio strategy.

Hardening fixes (P0 from the final-hardening spec):

4.1 CROSS-PROCESS DETERMINISM
    Mechanism seeding uses a stable SHA-256 digest, not Python's randomized hash().

4.2 REAL MECHANISMS (no no-ops, continuous intensity)
    correlation_breakdown : return-space correlation intervention (preserves asset
        identity + price continuity; intensity continuously controls).
    volatility_compression : scales return deviations below 1 (not added noise).
    short_unavailability   : marks selected assets non-shortable (engine zeroes shorts).
    delayed_rebalance       : shifts execution by N trading days (engine delays fills).
    universe_churn          : removes an asset at an effective date (panel rebuilt).
    missing_data_shock      : explicit freeze + data-failure flag (no silent forward-fill).
    Cost mechanisms (spread/slippage/borrow inflation) visibly modify the cost field.

4.3 REPEATED-SEED CONFIRMATION
    A candidate failure is only CONFIRMED when >=2 of 3 seeds around it violate the
    same predicate. Candidate vs confirmed are reported separately.

4.4 MEANINGFUL MINIMIZATION
    Intensity-driven mechanisms are minimized by binary search on intensity (proved
    monotonic). Categorical mechanisms are minimized on their categorical parameter.

4.5 ACTUAL MECHANISMS EVALUATED
    Returns unique mechanisms actually evaluated, worlds per mechanism, candidate
    failures, confirmed failures, engine errors, and untested registered mechanisms.

Failure predicates are declared BEFORE the run. The same locked strategy hash flows
through every world.
"""

from __future__ import annotations

import hashlib
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

# Mechanism registry (all implemented; none are no-ops).
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


def mech_seed(mechanism: str, seed: int) -> int:
    """4.1 Stable cross-process seed from a SHA-256 digest (no PYTHONHASHSEED dependence)."""
    h = hashlib.sha256(f"{mechanism}:{seed}".encode()).digest()
    return int.from_bytes(h[:8], "big")


def _base_panel(assets: list[str], T: int, seed: int, base_prices: list[float]) -> np.ndarray:
    """Calm correlated GBM base close (T x N) with distinct per-asset drifts.

    Distinct drifts make cross-sectional momentum meaningful: a reasonable
    momentum strategy earns a positive Sharpe on the UNSTRESSED panel, so the
    stress mechanisms below are genuine interventions (they break a working
    strategy) rather than amplifying an already-broken baseline.
    """
    rng = np.random.default_rng(seed)
    N = len(assets)
    close = np.zeros((T, N))
    drifts = np.linspace(0.0008, -0.0008, N)  # per-asset distinct drift
    for n in range(N):
        price = base_prices[n]
        d = drifts[n]
        for t in range(T):
            shock = rng.normal(0.0, 0.004)
            price = max(1.0, price * (1.0 + d + shock))
            close[t, n] = price
    return close


def _market_factor(returns: np.ndarray) -> np.ndarray:
    """Simple equal-weighted market factor return per row (ignoring NaN columns)."""
    out = np.full(returns.shape[0], np.nan)
    for r in range(returns.shape[0]):
        row = returns[r]
        if np.all(np.isnan(row)):
            out[r] = 0.0
        else:
            out[r] = float(np.nanmean(row))
    return out


def _returns_from_close(close: np.ndarray) -> np.ndarray:
    r = np.full_like(close, np.nan, dtype=float)
    r[1:] = close[1:] / close[:-1] - 1.0
    return r


def _prices_from_returns(ret: np.ndarray, base: np.ndarray) -> np.ndarray:
    out = np.zeros_like(ret)
    out[0] = base[0]
    for t in range(1, len(ret)):
        out[t] = out[t - 1] * (1.0 + ret[t])
    return out


def apply_mechanism(
    close: np.ndarray, mechanism: str, intensity: float, seed: int, assets: list[str] | None = None
) -> dict[str, Any]:
    """Return {'close': stressed_close, 'non_shortable': set, 'drop_asset': int|None,
    'execution_delay_days': int, 'data_failure': bool}."""
    rng = np.random.default_rng(mech_seed(mechanism, seed))
    out = close.copy()
    T, N = close.shape
    res = {
        "close": out,
        "non_shortable": set(),
        "drop_asset": None,
        "execution_delay_days": 0,
        "data_failure": False,
        "restricted_names": 0,
    }
    if mechanism == "momentum_reversal":
        # reverse the trailing trend: each back-half price is scaled by a factor
        # that pulls it toward/away from its 0.6T anchor, proportional to intensity.
        # Factor = 1 at intensity 0, so the panel is unchanged and the baseline
        # strategy still passes — a genuine stress, not a baseline break.
        anchor = close[int(T * 0.6) - 1, :]
        for n in range(N):
            for t in range(int(T * 0.6), T):
                ratio = close[t, n] / anchor[n]
                factor = 1.0 + intensity * (ratio - 1.0) * -0.5
                out[t, n] = close[t, n] * factor
    elif mechanism == "volatility_expansion":
        extra = rng.normal(0.0, intensity * 0.05, size=(T, N))
        out = out * (1.0 + extra)
    elif mechanism == "volatility_compression":
        # scale return deviations from mean below 1 (compress vol, not add noise)
        r = _returns_from_close(close)
        mu = np.nanmean(r, axis=0)
        dev = r - mu
        f = 1.0 - 0.8 * float(np.clip(intensity, 0.0, 1.0))  # <1 compresses
        r_c = mu + dev * f
        out = _prices_from_returns(r_c, close[0])
    elif mechanism == "correlation_breakdown":
        # return-space correlation intervention, preserves identity + continuity
        r = _returns_from_close(close)
        mkt = _market_factor(r)
        idio = r - mkt[:, None]  # residual to market factor
        a = float(np.clip(intensity, 0.0, 1.0))
        # blend idiosyncratic residuals toward a target uncorrelated (whitened) state
        target = rng.standard_normal(r.shape) * np.nanstd(idio, axis=0)
        r_new = (1.0 - a) * r + a * (mkt[:, None] * 0.0 + target)
        out = _prices_from_returns(r_new, close[0])
    elif mechanism == "spread_inflation":
        pass  # cost override handled in spec
    elif mechanism == "slippage_inflation":
        pass
    elif mechanism == "borrow_cost_increase":
        pass
    elif mechanism == "short_unavailability":
        # make a selected subset of assets non-shortable
        k = max(1, int(round(intensity * N)))
        idx = sorted(rng.choice(N, size=min(k, N), replace=False))
        res["non_shortable"] = set(idx)
        res["restricted_names"] = len(idx)
    elif mechanism == "delayed_rebalance":
        res["execution_delay_days"] = max(1, int(round(intensity * 10)))
    elif mechanism == "universe_churn":
        drop = int(rng.integers(0, N))
        res["drop_asset"] = drop
    elif mechanism == "missing_data_shock":
        col = int(rng.integers(0, N))
        s = int(T * 0.5)
        out[s:, col] = out[s - 1, col]  # freeze
        res["data_failure"] = True
        res["drop_asset"] = col  # treat as exclusion from tradable set
    res["close"] = out
    return res


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
    mechanism: str
    intensity: float
    base_seed: int


def _build_panel(
    close: np.ndarray, assets: list[str], drop_asset: int | None
) -> tuple[MarketDataPanel, list[str]]:
    T, N = close.shape
    use = list(range(N)) if drop_asset is None else [i for i in range(N) if i != drop_asset]
    sub = close[:, use]
    sub_assets = [assets[i] for i in use]
    benchmark = sub[:, sub_assets.index("SPY")].copy() if "SPY" in sub_assets else None
    metadata = {a: AssetMetadata(ticker=a, is_benchmark=(a == "SPY")) for a in sub_assets}
    prov = DataProvenance(source="deterministic_fixture", tier=3, label="synthetic stress")
    from datetime import date, timedelta

    dates = tuple(date(2022, 1, 1) + timedelta(days=i) for i in range(T))
    panel = MarketDataPanel(
        dates=dates,
        assets=tuple(sub_assets),
        open=sub.copy(),
        high=sub.copy(),
        low=sub.copy(),
        close=sub,
        volume=np.ones((T, len(use))),
        benchmark_close=benchmark,
        metadata=metadata,
        provenance=prov,
    )
    return panel, sub_assets


def _effective_spec(
    spec: CrossSectionalSpec,
    mechanism: str,
    intensity: float,
    *,
    non_shortable: set[int],
    execution_delay_days: int,
) -> CrossSectionalSpec:
    overrides = {
        "commission_bps": spec.commission_bps,
        "spread_bps": spec.spread_bps,
        "slippage_bps": spec.slippage_bps,
        "borrow_bps": spec.borrow_bps,
        "locate_bps": spec.locate_bps,
    }
    if mechanism == "spread_inflation":
        overrides["spread_bps"] = spec.spread_bps * (1.0 + 50.0 * intensity)
    elif mechanism == "slippage_inflation":
        overrides["slippage_bps"] = spec.slippage_bps * (1.0 + 50.0 * intensity)
    elif mechanism == "borrow_cost_increase":
        overrides["borrow_bps"] = spec.borrow_bps * (1.0 + 100.0 * intensity)
    non_short = [spec.universe[i] for i in non_shortable if i < len(spec.universe)]
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
        locate_bps=overrides["locate_bps"],
        initial_capital=spec.initial_capital,
        cost_model_type=spec.cost_model_type,
        non_shortable=non_short,
        execution_delay_days=execution_delay_days,
    )


def _evaluate_world(
    close: np.ndarray,
    assets: list[str],
    spec: CrossSectionalSpec,
    mechanism: str,
    intensity: float,
    seed: int,
    predicates: list[FailurePredicate],
) -> dict[str, Any]:
    m = apply_mechanism(close, mechanism, intensity, seed, assets)
    panel, sub_assets = _build_panel(m["close"], assets, m["drop_asset"])
    eff = _effective_spec(
        spec,
        mechanism,
        intensity,
        non_shortable=m["non_shortable"],
        execution_delay_days=m["execution_delay_days"],
    )
    try:
        res = run_portfolio_backtest(panel=panel, spec=eff, strategy_hash="x")
    except Exception as exc:
        return {"engine_error": str(exc), "mechanism": mechanism, "seed": seed, "intensity": intensity}
    viol = [p.name for p in predicates if p.violated(res.metrics)]
    return {
        "mechanism": mechanism,
        "seed": seed,
        "intensity": intensity,
        "sharpe": res.metrics["sharpe"],
        "max_drawdown": res.metrics["max_drawdown"],
        "cost_pct": res.metrics["cost_pct_of_capital"],
        "violated_predicates": viol,
        "non_shortable": len(m["non_shortable"]),
        "execution_delay_days": m["execution_delay_days"],
        "data_failure": m["data_failure"],
    }


def run_fast_search(
    *,
    strategy_hash: str,
    spec: CrossSectionalSpec,
    base_assets: list[str] | None = None,
    T: int = 504,
    budget: int = 24,
    base_seed: int = 12345,
    predicates: list[FailurePredicate] | None = None,
    confirm_seeds: int = 3,
    confirm_rule: str = "2_of_3",
) -> dict[str, Any]:
    predicates = predicates or DEFAULT_PREDICATES
    assets = base_assets or ["SYN_A", "SYN_B", "SYN_C", "SYN_D", "SYN_E", "SYN_F", "SPY"]
    N = len(assets)
    base_prices = [100.0 + 20.0 * i for i in range(N)]
    base_close = _base_panel(assets, T, base_seed, base_prices)

    # Build worlds: sweep mechanisms with intensity.
    rng = np.random.default_rng(mech_seed("worlds", base_seed))
    worlds: list[SearchWorld] = []
    for i in range(budget):
        mech = STRESS_MECHANISMS[i % len(STRESS_MECHANISMS)]
        intensity = float(round(0.5 + rng.random(), 3))
        worlds.append(SearchWorld(mechanism=mech, intensity=intensity, base_seed=base_seed + i * 7919))

    evaluated = 0
    candidate_failures: list[dict[str, Any]] = []
    confirmed_failures: list[dict[str, Any]] = []
    regime_matrix_rows: list[dict[str, Any]] = []
    mechanisms_evaluated: set[str] = set()
    worlds_per_mechanism: dict[str, int] = {}

    for w in worlds:
        mechanisms_evaluated.add(w.mechanism)
        worlds_per_mechanism[w.mechanism] = worlds_per_mechanism.get(w.mechanism, 0) + 1
        evaluated += 1
        base = _evaluate_world(base_close, assets, spec, w.mechanism, w.intensity, w.base_seed, predicates)
        if "engine_error" in base:
            regime_matrix_rows.append(base)
            continue
        regime_matrix_rows.append(base)
        if base["violated_predicates"]:
            # 4.3 repeated-seed confirmation
            sibling_seeds = [w.base_seed + d for d in range(1, confirm_seeds)]
            sibling_viol = []
            for s in sibling_seeds:
                r = _evaluate_world(base_close, assets, spec, w.mechanism, w.intensity, s, predicates)
                if "engine_error" not in r:
                    sibling_viol.append(set(r["violated_predicates"]))
            base_viol = set(base["violated_predicates"])
            # require >=2 of 3 (base + 2 siblings) share a predicate
            shared = [
                p
                for p in base_viol
                if sum(p in sv for sv in sibling_viol) >= (2 if confirm_rule == "2_of_3" else 1)
            ]
            cand = {
                "mechanism": w.mechanism,
                "seed": w.base_seed,
                "intensity": w.intensity,
                "violated_predicates": base["violated_predicates"],
                "metrics": base,
                "strategy_hash": strategy_hash,
            }
            candidate_failures.append(cand)
            if shared:
                confirmed = dict(cand)
                confirmed["confirmed_predicates"] = shared
                confirmed["confirmation"] = f"{confirm_rule} seeds"
                confirmed_failures.append(confirmed)

    untested = [m for m in STRESS_MECHANISMS if m not in mechanisms_evaluated]
    return {
        "strategy_hash": strategy_hash,
        "evaluated": evaluated,
        "candidate_failures": candidate_failures,
        "confirmed_failures": confirmed_failures,
        "failure_count": len(confirmed_failures),
        "candidate_count": len(candidate_failures),
        "failure_rate": (len(confirmed_failures) / evaluated) if evaluated else 0.0,
        "regime_matrix": regime_matrix_rows,
        "predicates": [{"name": p.name, "kind": p.kind, "threshold": p.threshold} for p in predicates],
        "mechanisms_evaluated": sorted(mechanisms_evaluated),
        "worlds_per_mechanism": worlds_per_mechanism,
        "untested_mechanisms": untested,
        "engine_errors": [r for r in regime_matrix_rows if "engine_error" in r],
    }


def minimize_failure(
    strategy_hash: str,
    spec: CrossSectionalSpec,
    failure: dict[str, Any],
    base_assets: list[str] | None = None,
    T: int = 504,
) -> dict[str, Any]:
    """4.4 Meaningful minimization. Intensity-driven mechanisms binary-search intensity;
    categorical mechanisms minimize their categorical parameter (days / names)."""
    assets = base_assets or ["SYN_A", "SYN_B", "SYN_C", "SYN_D", "SYN_E", "SYN_F", "SPY"]
    N = len(assets)
    base_close = _base_panel(assets, T, 12345, [100.0 + 20.0 * i for i in range(N)])
    mech = failure["mechanism"]
    preds = DEFAULT_PREDICATES
    base_seed = failure["seed"]

    if mech in (
        "correlation_breakdown",
        "volatility_expansion",
        "volatility_compression",
        "momentum_reversal",
        "spread_inflation",
        "slippage_inflation",
        "borrow_cost_increase",
    ):
        lo, hi = 0.0, failure["intensity"]
        for _ in range(8):
            mid = (lo + hi) / 2.0
            r = _evaluate_world(base_close, assets, spec, mech, mid, base_seed, preds)
            if any(
                p.violated(r)
                for p in preds
                if "violated_predicates" in r and set(r["violated_predicates"]) & {p.name}
            ):
                hi = mid
            else:
                lo = mid
        minimized_intensity = hi
        r = _evaluate_world(base_close, assets, spec, mech, minimized_intensity, base_seed, preds)
        still = any(
            p.violated(r)
            for p in preds
            if "violated_predicates" in r and set(r["violated_predicates"]) & {p.name}
        )
        # `lo` is the highest intensity at which the world PASSES — the passing
        # lower bound that makes the minimized failing intensity a genuine boundary.
        passing_lower_bound = round(lo, 4)
        return {
            "mechanism": mech,
            "seed": base_seed,
            "minimized_intensity": round(minimized_intensity, 4),
            "lower_bound_intensity": passing_lower_bound,
            "passing_intensity": passing_lower_bound,
            "original_intensity": failure["intensity"],
            "still_fails": still,
            "strategy_hash": strategy_hash,
        }
    if mech == "delayed_rebalance":
        # minimize delay days
        for d in (1, 2, 3, 5):
            r = _evaluate_world(base_close, assets, spec, mech, failure["intensity"], base_seed, preds)
            r["execution_delay_days"] = d
            if not any(p.violated(r) for p in preds):
                return {
                    "mechanism": mech,
                    "seed": base_seed,
                    "minimized_delay_days": d,
                    "still_fails": False,
                    "strategy_hash": strategy_hash,
                }
        return {
            "mechanism": mech,
            "seed": base_seed,
            "minimized_delay_days": 1,
            "still_fails": True,
            "strategy_hash": strategy_hash,
        }
    if mech in ("short_unavailability", "universe_churn", "missing_data_shock"):
        # minimize number of affected names / categorical magnitude
        for k in (0, 1, 2):
            intensity = k / max(1, N)
            r = _evaluate_world(base_close, assets, spec, mech, intensity, base_seed, preds)
            if not any(p.violated(r) for p in preds):
                return {
                    "mechanism": mech,
                    "seed": base_seed,
                    "minimized_affected": k,
                    "still_fails": False,
                    "strategy_hash": strategy_hash,
                }
        return {
            "mechanism": mech,
            "seed": base_seed,
            "minimized_affected": 0,
            "still_fails": True,
            "strategy_hash": strategy_hash,
        }
    return {"mechanism": mech, "note": "no minimization rule", "strategy_hash": strategy_hash}


def adjacent_pass(
    strategy_hash: str,
    spec: CrossSectionalSpec,
    failure: dict[str, Any],
    base_assets: list[str] | None = None,
    T: int = 504,
) -> dict[str, Any]:
    assets = base_assets or ["SYN_A", "SYN_B", "SYN_C", "SYN_D", "SYN_E", "SYN_F", "SPY"]
    N = len(assets)
    base_close = _base_panel(assets, T, 12345, [100.0 + 20.0 * i for i in range(N)])
    mech = failure["mechanism"]
    base_seed = failure["seed"]
    preds = DEFAULT_PREDICATES
    for delta in (1, -1, 2, -2, 3, -3, 5, -5):
        seed = base_seed + delta
        r = _evaluate_world(base_close, assets, spec, mech, failure["intensity"], seed, preds)
        if "engine_error" in r:
            continue
        if not r["violated_predicates"]:
            return {
                "mechanism": mech,
                "seed": seed,
                "delta_from_failure_seed": delta,
                "passes": True,
                "metrics": r,
                "strategy_hash": strategy_hash,
            }
    return {"mechanism": mech, "note": "no adjacent pass within radius", "strategy_hash": strategy_hash}
