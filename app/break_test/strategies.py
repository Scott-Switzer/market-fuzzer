from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

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


@dataclass(frozen=True)
class BreakoutStrategy:
    entry_lookback: int
    exit_lookback: int

    def positions(self, prices: np.ndarray) -> np.ndarray:
        if self.entry_lookback < 3 or len(prices) <= self.entry_lookback:
            raise ValueError("Entry lookback must be at least 3 and shorter than the price history")
        result = np.zeros(len(prices))
        for index in range(self.entry_lookback, len(prices)):
            result[index] = (
                1.0
                if prices[index] > np.max(prices[index - self.entry_lookback : index])
                else result[index - 1]
            )
            if prices[index] < np.min(prices[index - self.exit_lookback : index]):
                result[index] = 0.0
        return result


@dataclass(frozen=True)
class RsiReversionStrategy:
    period: int
    oversold: int = 30
    overbought: int = 70

    def positions(self, prices: np.ndarray) -> np.ndarray:
        if self.period < 2 or len(prices) <= self.period:
            raise ValueError("RSI period must be at least 2 and shorter than the price history")
        changes = np.diff(prices, prepend=prices[0])
        result = np.zeros(len(prices))
        for index in range(self.period, len(prices)):
            window = changes[index - self.period + 1 : index + 1]
            gains = np.mean(np.clip(window, 0, None))
            losses = np.mean(np.clip(-window, 0, None))
            rsi = 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)
            result[index] = (
                1.0 if rsi < self.oversold else (0.0 if rsi > self.overbought else result[index - 1])
            )
        return result


class StrategyFn(Protocol):
    name: str
    type_id: str
    default_params: dict[str, int]

    def positions(self, prices: np.ndarray, **params: int) -> np.ndarray: ...


BUILTIN_STRATEGIES: dict[str, dict[str, object]] = {
    "sma_crossover": {
        "name": "SMA Crossover",
        "description": "Long when fast SMA crosses above slow SMA, flat otherwise.",
        "default_params": {"fast": 20, "slow": 50},
        "param_ranges": {"fast": (2, 200), "slow": (3, 500)},
    },
    "breakout": {
        "name": "Breakout Momentum",
        "description": "Enter on new highs, exit on pullback to recent low.",
        "default_params": {"entry_lookback": 20, "exit_lookback": 10},
        "param_ranges": {"entry_lookback": (3, 200), "exit_lookback": (2, 100)},
    },
    "rsi_reversion": {
        "name": "RSI Mean Reversion",
        "description": "Buy when oversold, sell when overbought.",
        "default_params": {"period": 14, "oversold": 30, "overbought": 70},
        "param_ranges": {"period": (2, 100), "oversold": (5, 45), "overbought": (55, 95)},
    },
}


def compute_positions(strategy_type: str, prices: np.ndarray, **params: int) -> np.ndarray:
    if strategy_type == "sma_crossover":
        return SmaStrategy(fast=params.get("fast", 20), slow=params.get("slow", 50)).positions(prices)
    if strategy_type == "breakout":
        return BreakoutStrategy(
            entry_lookback=params.get("entry_lookback", 20),
            exit_lookback=params.get("exit_lookback", 10),
        ).positions(prices)
    if strategy_type == "rsi_reversion":
        return RsiReversionStrategy(
            period=params.get("period", 14),
            oversold=params.get("oversold", 30),
            overbought=params.get("overbought", 70),
        ).positions(prices)
    raise ValueError(f"Unsupported strategy type: {strategy_type}")
