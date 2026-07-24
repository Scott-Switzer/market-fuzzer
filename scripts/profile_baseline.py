from __future__ import annotations

import time

import numpy as np

from app.break_test.metrics import backtest_metrics
from app.break_test.strategies import compute_positions


def _compute_world_metrics(
    prices: np.ndarray, strategy_type: str, params: dict[str, int]
) -> dict[str, float | int]:
    return backtest_metrics(prices, compute_positions(strategy_type, prices, **params))


def main() -> None:
    t0 = time.perf_counter()
    prices = np.array([100.0] * 120, dtype=float)
    positions = compute_positions("sma_crossover", prices, fast=20, slow=50)
    ms = backtest_metrics(prices, positions)
    print("metrics_total", float(ms["total_return_pct"]))
    print("wall", round(time.perf_counter() - t0, 4))


if __name__ == "__main__":
    main()
