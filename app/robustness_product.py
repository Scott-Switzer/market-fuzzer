from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SmaStrategy:
    fast: int
    slow: int

    def positions(self, prices: np.ndarray) -> np.ndarray:
        if self.fast < 2 or self.slow <= self.fast or len(prices) <= self.slow:
            raise ValueError("Use at least slow window + 1 prices, with 2 <= fast < slow")
        fast = np.convolve(prices, np.ones(self.fast) / self.fast, mode="valid")
        slow = np.convolve(prices, np.ones(self.slow) / self.slow, mode="valid")
        aligned_fast = fast[self.slow - self.fast :]
        signal = (aligned_fast > slow).astype(float)
        return np.concatenate((np.zeros(self.slow - 1), signal))


def _metrics(prices: np.ndarray, positions: np.ndarray, fee_bps: float = 2.0) -> dict[str, float | int]:
    returns = np.diff(prices) / prices[:-1]
    held = positions[:-1]
    turnover = np.abs(np.diff(positions, prepend=0.0))[:-1]
    strategy_returns = held * returns - turnover * fee_bps / 10_000
    equity = np.cumprod(1 + strategy_returns)
    peaks = np.maximum.accumulate(equity)
    drawdown = equity / peaks - 1
    std = float(np.std(strategy_returns, ddof=1)) if len(strategy_returns) > 1 else 0.0
    sharpe = float(np.mean(strategy_returns) / std * math.sqrt(252)) if std > 0 else 0.0
    trades = int(np.sum(np.diff(positions, prepend=0.0) > 0))
    return {
        "total_return_pct": round((float(equity[-1]) - 1) * 100, 2),
        "max_drawdown_pct": round(float(np.min(drawdown)) * 100, 2),
        "sharpe": round(sharpe, 2),
        "trades": trades,
        "turnover": round(float(np.sum(turnover)), 2),
    }


def evaluate_sma_robustness(
    closes: list[float], *, fast: int = 20, slow: int = 50, worlds_per_regime: int = 30
) -> dict[str, object]:
    prices = np.asarray(closes, dtype=float)
    if len(prices) < max(80, slow + 2) or not np.all(np.isfinite(prices)) or np.any(prices <= 0):
        raise ValueError("Provide at least 80 finite, positive closing prices")
    strategy = SmaStrategy(fast=fast, slow=slow)
    historical = _metrics(prices, strategy.positions(prices))
    base_returns = np.diff(np.log(prices))
    base_vol = max(float(np.std(base_returns)), 0.0001)
    regimes = {
        "steady trend": (0.0005, 0.7, 0.0),
        "sideways and choppy": (0.0, 1.25, -0.35),
        "high volatility": (0.0, 2.2, 0.0),
        "sudden selloff": (-0.0012, 1.8, 0.15),
    }
    regime_results: list[dict[str, object]] = []
    for regime_index, (name, (drift, vol_mult, reversal)) in enumerate(regimes.items()):
        returns: list[float] = []
        drawdowns: list[float] = []
        losses = 0
        for world in range(worlds_per_regime):
            rng = np.random.default_rng(10_000 + regime_index * 1_000 + world)
            shocks = rng.normal(drift, base_vol * vol_mult, len(prices) - 1)
            if reversal:
                for index in range(1, len(shocks)):
                    shocks[index] += reversal * -shocks[index - 1]
            synthetic = prices[0] * np.exp(np.concatenate(([0.0], np.cumsum(shocks))))
            result = _metrics(synthetic, strategy.positions(synthetic))
            value = float(result["total_return_pct"])
            returns.append(value)
            drawdowns.append(float(result["max_drawdown_pct"]))
            losses += value < 0
        regime_results.append({
            "regime": name,
            "worlds": worlds_per_regime,
            "loss_rate_pct": round(losses / worlds_per_regime * 100, 1),
            "median_return_pct": round(float(np.median(returns)), 2),
            "worst_drawdown_pct": round(float(np.min(drawdowns)), 2),
        })
    weakest = max(regime_results, key=lambda item: float(item["loss_rate_pct"]))
    return {
        "strategy": {"name": "SMA crossover", "fast_window": fast, "slow_window": slow},
        "historical_backtest": historical,
        "synthetic_forward_test": {"worlds": worlds_per_regime * len(regimes), "regimes": regime_results},
        "failure_summary": (
            f"The strategy was most vulnerable in {weakest['regime']} markets: "
            f"it lost money in {weakest['loss_rate_pct']}% of {weakest['worlds']} unseen worlds, "
            f"with a worst drawdown of {weakest['worst_drawdown_pct']}%."
        ),
        "limitations": "Synthetic regimes are diagnostic models, not forecasts. Historical results depend on the uploaded data and include a 2 bps turnover cost.",
    }
