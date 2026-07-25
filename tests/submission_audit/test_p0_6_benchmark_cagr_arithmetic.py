"""P0-6: Benchmark "CAGR" is arithmetic mean * 252 (engine.py line ~439).

compute_portfolio_metrics: `bench_cagr = bmean * 252.0`. That is an
annualized ARITHMETIC mean, not a CAGR, while the strategy's own cagr uses the
correct geometric formula `(equity[-1]/cap)**(252/n) - 1`. The benchmark
comparison is therefore apples-to-oranges and systematically FLATTERS the
benchmark by ~ +0.5*sigma^2/yr (volatility drag omitted).

HAND CALC (alternating +10%/-10% benchmark, 253 closes = 252 daily returns):
  Each +10/-10 pair multiplies price by 0.99. After 126 pairs:
    b[-1]/b[0] = 0.99**126 = 0.28215...
    correct CAGR = (0.99**126)**(252/252) - 1 = -0.71785  (LOSES 72%)
  ENGINE: bmean = mean(+0.10, -0.10, ...) = 0.0 -> bench_cagr = 0.0 * 252 = 0.
  A benchmark that lost 72% of its value is reported as flat.

FIX SIGNATURE: in compute_portfolio_metrics:
  nb = len(brets)
  bench_cagr = (benchmark_close[-1] / benchmark_close[0]) ** (252.0 / nb) - 1.0
(matching the geometric formula already used for the strategy's cagr).
"""

import numpy as np
import pytest

from app.strategy_lab.submission.engine import compute_portfolio_metrics


@pytest.mark.xfail(strict=False, reason="P0-6: benchmark CAGR = mean*252, not geometric (engine.py ~439)")
def test_benchmark_cagr_is_geometric():
    n = 252  # one year of daily returns
    rets = np.tile([0.10, -0.10], n // 2)
    bench = 100.0 * np.concatenate([[1.0], np.cumprod(1.0 + rets)])
    # trivial flat portfolio so only the benchmark math is exercised
    T = n + 1
    equity = np.full(T, 1_000_000.0)
    shares = np.zeros((T, 1))
    close = np.full((T, 1), 100.0)
    zeros = np.zeros(T)
    m = compute_portfolio_metrics(
        equity=equity,
        shares=shares,
        close=close,
        cap=1_000_000.0,
        benchmark_close=bench,
        cost_summary={
            "commission": 0.0,
            "slippage": 0.0,
            "spread": 0.0,
            "borrow": 0.0,
            "locate": 0.0,
            "total": 0.0,
        },
        gross_exp=zeros,
        net_exp=zeros,
        turnover=zeros,
    )
    expected = (bench[-1] / bench[0]) ** (252.0 / n) - 1.0  # = 0.99**126 - 1 ~ -0.7178
    assert m["benchmark_cagr"] is not None
    assert abs(m["benchmark_cagr"] - expected) < 1e-3, (
        f"BENCHMARK DEFECT: benchmark lost {1 - bench[-1] / bench[0]:.0%} "
        f"(true CAGR {expected:.2%}) but engine reports "
        f"benchmark_cagr={m['benchmark_cagr']:.2%} (arithmetic mean*252)."
    )
