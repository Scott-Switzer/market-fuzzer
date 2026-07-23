from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.break_test.metrics import backtest_metrics
from app.break_test.strategies import compute_positions
from app.break_test.synthetic_market import ResearchSyntheticMarketGenerator

_GENERATOR = ResearchSyntheticMarketGenerator()
_REGIME_KEYS = tuple(r.key for r in _GENERATOR.regimes)
_REGIME_LABELS = {r.key: r.label for r in _GENERATOR.regimes}
_REGIME_SPECS: dict[str, dict[str, Any]] = {
    "steady_trend": {"drift": 0.08, "vol": 0.15, "reversal": 0.0},
    "sideways_choppy": {"drift": 0.0, "vol": 0.22, "reversal": -0.35},
    "high_volatility": {"drift": 0.0, "vol": 0.65, "reversal": 0.0},
    "sudden_selloff": {"drift": -0.25, "vol": 0.85, "reversal": 0.15},
    "low_vol": {"drift": 0.06, "vol": 0.10, "reversal": 0.0},
    "high_vol": {"drift": 0.0, "vol": 0.65, "reversal": 0.0},
    "crisis": {"drift": -0.30, "vol": 1.10, "reversal": 0.20},
}

# Public aliases preserved for downstream modules such as quant_validation.
REGIME_KEYS = _REGIME_KEYS
REGIME_LABELS = _REGIME_LABELS
SYNTHETIC_REGIMES = _REGIME_SPECS


def build_world_price_path(
    regime_key: str,
    seed: int,
    target_asset: str = "SYNTH",
    length: int = 120,
    base_price: float = 100.0,
) -> dict[str, object]:
    return _GENERATOR.generate_path(
        regime_key=regime_key,
        seed=seed,
        length=length,
        base_price=base_price,
        target_asset=target_asset,
    )


def run_forward_test(
    prices: list[float],
    strategy_type: str,
    params: dict[str, int],
    worlds_per_regime: int = 100,
) -> list[dict[str, object]]:
    px = np.array(prices, dtype=float)
    length = len(px)
    results: list[dict[str, object]] = []
    for regime_index, key in enumerate(_REGIME_KEYS):
        returns_list: list[float] = []
        drawdowns: list[float] = []
        losses = 0
        positions: list[float] = []
        for world in range(worlds_per_regime):
            seed = 40_000 + regime_index * 1_000 + world
            path = build_world_price_path(
                key,
                seed=seed,
                length=length,
                base_price=float(px[0]),
            )
            if len(path["prices"]) < 5:
                continue
            syn = np.array(path["prices"], dtype=float)
            positions = compute_positions(strategy_type, syn, **params)
            metrics = backtest_metrics(syn, positions)
            value = float(metrics["total_return_pct"])
            returns_list.append(value)
            drawdowns.append(float(metrics["max_drawdown_pct"]))
            losses += value < 0
        if not returns_list:
            continue
        results.append(
            {
                "regime": _REGIME_LABELS[key],
                "worlds": len(returns_list),
                "loss_rate_pct": round(losses / len(returns_list) * 100, 1),
                "median_return_pct": round(float(np.median(returns_list)), 2),
                "mean_return_pct": round(float(np.mean(returns_list)), 2),
                "worst_drawdown_pct": round(float(min(drawdowns)), 2),
                "best_return_pct": round(float(max(returns_list)), 2),
            }
        )
    return results


def detect_regimes(prices: list[float]) -> dict[str, object]:
    px = np.array(prices, dtype=float)
    log_returns = np.diff(np.log(px))
    vol = float(np.std(log_returns, ddof=1)) * math.sqrt(252) if len(log_returns) > 1 else 0.0
    avg_return = float(np.mean(log_returns) * 252) if len(log_returns) else 0.0
    high_vol_periods = 0.0
    if len(log_returns) >= 20:
        rolling_var = np.convolve(log_returns**2, np.ones(20) / 20, mode="valid")
        rolling_std = np.sqrt(np.maximum(rolling_var, 1e-20))
        median_std = float(np.median(rolling_std))
        if median_std > 1e-9:
            high_vol_periods = float(np.mean(rolling_std > 1.5 * median_std)) * 100
    if vol < 0.12:
        regime = "low-vol / likely trend or range"
    elif vol < 0.22:
        regime = "normal-vol / mixed"
    elif vol < 0.35:
        regime = "elevated-vol / stress risk"
    else:
        regime = "crisis-vol / tail risk"
    return {
        "regime": regime,
        "detected_drift": round(avg_return * 100, 2),
        "detected_volatility": round(vol * 100, 2),
        "high_vol_periods_pct": round(high_vol_periods, 1),
        "length": len(px),
    }
