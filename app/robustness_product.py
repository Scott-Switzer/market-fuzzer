from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.break_test.metrics import backtest_metrics, compute_equity_curve


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


def _strategy_positions(kind: str, prices: np.ndarray, fast: int, slow: int) -> tuple[str, np.ndarray]:
    if kind == "sma_crossover":
        return "SMA crossover", SmaStrategy(fast=fast, slow=slow).positions(prices)
    if kind == "breakout":
        if slow < 3 or len(prices) <= slow:
            raise ValueError("Breakout lookback must be at least 3 and shorter than the price history")
        positions = np.zeros(len(prices))
        for index in range(slow, len(prices)):
            positions[index] = (
                1.0 if prices[index] > np.max(prices[index - slow : index]) else positions[index - 1]
            )
            if prices[index] < np.min(prices[index - fast : index]):
                positions[index] = 0.0
        return "Breakout momentum", positions
    if kind == "rsi_reversion":
        if fast < 2 or len(prices) <= fast:
            raise ValueError("RSI period must be at least 2 and shorter than the price history")
        changes = np.diff(prices, prepend=prices[0])
        positions = np.zeros(len(prices))
        for index in range(fast, len(prices)):
            window = changes[index - fast + 1 : index + 1]
            gains = np.mean(np.clip(window, 0, None))
            losses = np.mean(np.clip(-window, 0, None))
            rsi = 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)
            positions[index] = 1.0 if rsi < 30 else (0.0 if rsi > 70 else positions[index - 1])
        return "RSI mean reversion", positions
    raise ValueError("Unsupported strategy type")


def _metrics(prices: np.ndarray, positions: np.ndarray, exchange_spec: object | None = None) -> dict[str, float | int]:
    import numpy as np

    px = np.asarray(prices, dtype=float)
    pos = np.asarray(positions, dtype=float)
    returns = np.diff(px) / px[:-1]
    held = pos[:-1]
    turnover = np.abs(np.diff(pos, prepend=0.0))[:-1]
    if exchange_spec is None:
        costs = turnover * 2.0 / 10_000
    else:
        from app.break_test.metrics import compute_turnover_cost
        costs = np.asarray(compute_turnover_cost(px, pos, exchange_spec=exchange_spec))
        if costs.size != returns.size:
            costs = np.resize(costs, returns.size)
    strategy_returns = held * returns - costs
    equity = np.cumprod(1 + strategy_returns)
    peaks = np.maximum.accumulate(equity)
    drawdown = equity / peaks - 1
    std = float(np.std(strategy_returns, ddof=1)) if len(strategy_returns) > 1 else 0.0
    sharpe = float(np.mean(strategy_returns) / std * math.sqrt(252)) if std > 0 else 0.0
    trades = int(np.sum(np.diff(pos, prepend=0.0) > 0))
    entries = np.flatnonzero(np.diff(pos, prepend=0.0) > 0)
    exits = np.flatnonzero(np.diff(pos, append=0.0) < 0)
    trade_returns = [
        prices[exit_] / prices[entry] - 1 for entry, exit_ in zip(entries, exits) if exit_ > entry
    ]
    win_rate = sum(value > 0 for value in trade_returns) / len(trade_returns) * 100 if trade_returns else 0.0
    return {
        "total_return_pct": round((float(equity[-1]) - 1) * 100, 2),
        "max_drawdown_pct": round(float(np.min(drawdown)) * 100, 2),
        "sharpe": round(sharpe, 2),
        "trades": trades,
        "win_rate_pct": round(win_rate, 1),
        "turnover": round(float(np.sum(turnover)), 2),
    }


def evaluate_sma_robustness(
    closes: list[float],
    *,
    fast: int = 20,
    slow: int = 50,
    worlds_per_regime: int = 30,
    strategy_type: str = "sma_crossover",
) -> dict[str, object]:
    prices = np.asarray(closes, dtype=float)
    if len(prices) < max(80, slow + 2) or not np.all(np.isfinite(prices)) or np.any(prices <= 0):
        raise ValueError("Provide at least 80 finite, positive closing prices")
    strategy_name, historical_positions = _strategy_positions(strategy_type, prices, fast, slow)
    historical = _metrics(prices, historical_positions)
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
            _, synthetic_positions = _strategy_positions(strategy_type, synthetic, fast, slow)
            result = _metrics(synthetic, synthetic_positions)
            value = float(result["total_return_pct"])
            returns.append(value)
            drawdowns.append(float(result["max_drawdown_pct"]))
            losses += value < 0
        regime_results.append(
            {
                "regime": name,
                "worlds": worlds_per_regime,
                "loss_rate_pct": round(losses / worlds_per_regime * 100, 1),
                "median_return_pct": round(float(np.median(returns)), 2),
                "worst_drawdown_pct": round(float(np.min(drawdowns)), 2),
            }
        )
    weakest = max(regime_results, key=lambda item: float(item["loss_rate_pct"]))
    suggested_slow = max(slow + 10, round(slow * 1.5))
    return {
        "strategy": {"name": strategy_name, "type": strategy_type, "fast_window": fast, "slow_window": slow},
        "historical_backtest": historical,
        "synthetic_forward_test": {
            "worlds": worlds_per_regime * len(regimes),
            "regimes": regime_results,
        },
        "failure_summary": (
            f"The strategy was most vulnerable in {weakest['regime']} markets: "
            f"it lost money in {weakest['loss_rate_pct']}% of {weakest['worlds']} unseen worlds, "
            f"with a worst drawdown of {weakest['worst_drawdown_pct']}%."
        ),
        "suggested_test": {
            "fast_window": fast,
            "slow_window": suggested_slow,
            "reason": "A slower confirmation window may reduce repeated entries in noisy markets. Test it as a comparison; it is not guaranteed to improve performance.",
        },
        "limitations": "Synthetic regimes are diagnostic models, not forecasts. Historical results include a 2 bps turnover cost baseline.",
    }
