from __future__ import annotations

import math

import numpy as np


def backtest_metrics(
    prices: np.ndarray,
    positions: np.ndarray,
    fee_bps: float = 2.0,
) -> dict[str, float | int]:
    px = np.asarray(prices, dtype=float)
    pos = np.asarray(positions, dtype=float)
    returns = np.diff(px) / px[:-1]
    held = pos[:-1]
    turnover = np.abs(np.diff(pos, prepend=0.0))[:-1]
    strategy_returns = held * returns - turnover * fee_bps / 10_000
    equity = np.cumprod(1 + strategy_returns)
    peaks = np.maximum.accumulate(equity)
    drawdown = equity / peaks - 1

    std = float(np.std(strategy_returns, ddof=1)) if len(strategy_returns) > 1 else 0.0
    mean = float(np.mean(strategy_returns))
    sharpe = mean / std * math.sqrt(252) if std > 0 else 0.0
    downside = strategy_returns[strategy_returns < 0]
    downside_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else 0.0
    sortino = mean / downside_std * math.sqrt(252) if downside_std > 0 else (-1.0 if mean <= 0 else 0.0)
    max_dd = float(np.min(drawdown))
    calmar = (float(equity[-1]) - 1) / (-max_dd) if max_dd < 0 else 0.0
    under = np.where(drawdown < 0)[0]
    max_dd_duration = 0.0
    if under.size:
        lengths = np.diff(np.where(np.concatenate(([under[0]], np.diff(under) > 1, [True])))[0])
        max_dd_duration = float(np.max(lengths))

    bench_returns = returns
    bench = np.cumprod(1 + bench_returns)
    bench_std = float(np.std(bench_returns, ddof=1)) if len(bench_returns) > 1 else 0.0
    bench_sharpe = float(np.mean(bench_returns) / bench_std * math.sqrt(252)) if bench_std > 0 else 0.0
    cov = float(np.cov(strategy_returns, bench_returns, ddof=1)[0, 1]) if len(bench_returns) > 1 else 0.0
    var_b = float(np.var(bench_returns, ddof=1))
    beta = cov / var_b if var_b > 0 else 0.0
    alpha = float(np.mean(strategy_returns - bench_returns))

    trades = int(np.sum(np.diff(pos, prepend=0.0) > 0))
    entries = np.flatnonzero(np.diff(pos, prepend=0.0) > 0)
    exits = np.flatnonzero(np.diff(pos, append=0.0) < 0)
    trade_returns = [float(px[exit_] / px[entry] - 1) for entry, exit_ in zip(entries, exits) if exit_ > entry and px[entry] > 0]
    win_rate = sum(1 for r in trade_returns if r > 0) / len(trade_returns) * 100 if trade_returns else 0.0
    wins = [r for r in trade_returns if r > 0]
    losses = [r for r in trade_returns if r < 0]
    profit_factor = float(sum(wins) / abs(sum(losses))) if wins and losses else 0.0

    var_95 = float(np.percentile(strategy_returns, 5)) if len(strategy_returns) else 0.0
    cvar_95 = float(np.mean(strategy_returns[strategy_returns <= var_95])) if len(strategy_returns) else 0.0

    return {
        "total_return_pct": round((float(equity[-1]) - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_dd_duration_days": round(max_dd_duration, 1),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "calmar": round(calmar, 2),
        "trades": trades,
        "turnover": round(float(np.sum(turnover)), 2),
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "benchmark_total_return_pct": round((float(bench[-1]) - 1) * 100, 2),
        "benchmark_sharpe": round(bench_sharpe, 2),
        "alpha": round(alpha * 252, 4),
        "beta": round(beta, 2),
        "avg_trade_return_pct": round(sum(trade_returns) / len(trade_returns) * 100, 2) if trade_returns else 0.0,
        "expectancy": round(float(np.mean(trade_returns)) if trade_returns else 0.0, 4),
        "var_95_pct": round(var_95 * 100, 2),
        "cvar_95_pct": round(cvar_95 * 100, 2),
    }


def compute_equity_curve(prices: np.ndarray, positions: np.ndarray, fee_bps: float = 2.0) -> list[float]:
    px = np.asarray(prices, dtype=float)
    pos = np.asarray(positions, dtype=float)
    returns = np.diff(px) / px[:-1]
    held = pos[:-1]
    turnover = np.abs(np.diff(pos, prepend=0.0))[:-1]
    strategy_returns = held * returns - turnover * fee_bps / 10_000
    equity = np.cumprod(1 + strategy_returns)
    return [round(float(v), 6) for v in equity]
