"""Generic portfolio accounting simulator (reset brief Phase 2 items 15, 24).

This engine knows NOTHING about momentum, quantiles, SMAs, or any strategy
feature. It consumes a ``TargetPlan`` (T x N target weights + rebalance mask)
and a ``MarketDataPanel`` and produces a ``BacktestResult``.

Responsibilities (and ONLY these):
  target -> order conversion, next-open fills, cash, shares, commission, spread,
  slippage, locate, daily borrow, turnover, gross/net exposure, benchmark, and
  the equity identity ``equity[t] == cash[t] + Σ shares[t,n]*close[t,n]``.

Execution model (matches the audited legacy engine):
  * Targets decided at close t (a decision bar) are executed at open t+1.
  * Sizing uses PRE-TRADE portfolio equity marked at the decision close.
  * Half-spread embedded in exec price; commission/slippage on notional;
    locate on newly-opened shorts; borrow accrues daily on held shorts.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.strategies.contracts import TargetPlan


@dataclass(frozen=True)
class CostModel:
    """Labeled heuristic cost model (bps). Not broker-calibrated."""

    commission_bps: float = 5.0
    spread_bps: float = 2.0
    slippage_bps: float = 3.0
    borrow_bps: float = 50.0  # annualized short financing
    locate_bps: float = 10.0  # one-time fee on newly opened shorts
    model_type: str = "heuristic_flat_bps"
    calibrated: bool = False


@dataclass
class BacktestResult:
    backtest_id: str
    strategy_hash: str
    dates: list[str]
    assets: list[str]
    target_weights: np.ndarray  # T x N as decided (carried from the plan)
    executed_weights: np.ndarray  # T x N actually held after fill
    shares: np.ndarray
    equity_curve: np.ndarray
    cash: np.ndarray
    gross_exposure: np.ndarray
    net_exposure: np.ndarray
    turnover: np.ndarray
    cost_summary: dict[str, float]
    metrics: dict[str, Any]
    daily_borrow: np.ndarray
    trades: list[dict[str, Any]]
    warnings: list[str]
    provenance: dict[str, Any] = field(default_factory=dict)
    benchmark_close: np.ndarray | None = None


def run_accounting(
    *,
    plan: TargetPlan,
    open_: np.ndarray,
    close: np.ndarray,
    dates: list[str],
    assets: list[str],
    initial_capital: float = 1_000_000.0,
    cost_model: CostModel | None = None,
    benchmark_close: np.ndarray | None = None,
    non_shortable_idx: list[int] | None = None,
    execution_delay_days: int = 0,
    provenance: dict[str, Any] | None = None,
) -> BacktestResult:
    """Simulate the plan against next-open fills. Pure accounting; no strategy logic."""
    cm = cost_model or CostModel()
    if cm.model_type != "heuristic_flat_bps":
        raise ValueError(f"unsupported cost model: {cm.model_type}")
    T, N = close.shape
    if T < 2:
        raise ValueError("need at least 2 dates for a backtest")
    if plan.target_weights.shape != (T, N):
        raise ValueError(f"target_weights shape {plan.target_weights.shape} != panel (T={T}, N={N})")

    # The active target held from bar t is the most recent decision-bar target
    # as of t-1 (next-open execution), shifted by any execution delay.
    signal = plan.target_weights
    rebal = plan.rebalance_mask
    latest = np.zeros(N, dtype=float)
    signal_on_date = np.zeros((T, N), dtype=float)
    for t in range(T):
        if rebal[t] and np.all(np.isfinite(signal[t])):
            latest = signal[t]
        signal_on_date[t] = latest

    ns = set(non_shortable_idx or [])
    if ns:
        for t in range(T):
            for i in ns:
                if signal_on_date[t, i] < 0:
                    signal_on_date[t, i] = 0.0

    delay = int(execution_delay_days or 0)
    active_target = np.zeros((T, N), dtype=float)
    for t in range(1, T):
        src = t - 1 - delay
        if src >= 0:
            active_target[t] = signal_on_date[src]

    shares = np.zeros((T, N), dtype=float)
    cash = np.zeros(T, dtype=float)
    equity = np.zeros(T, dtype=float)
    daily_borrow = np.zeros(T, dtype=float)
    cash[0] = float(initial_capital)
    equity[0] = cash[0]
    trades: list[dict[str, Any]] = []
    totals = {"commission": 0.0, "slippage": 0.0, "spread": 0.0, "borrow": 0.0, "locate": 0.0}
    prev_shares = np.zeros(N, dtype=float)
    half_spread = cm.spread_bps / 10_000.0 / 2.0

    for t in range(1, T):
        fill_px = open_[t]
        mark_prev = close[t - 1]
        pre_trade_equity = cash[t - 1] + float(np.sum(prev_shares * mark_prev))
        val_px = np.where(fill_px > 0, fill_px, mark_prev)
        val_px = np.where(val_px > 0, val_px, 1.0)
        target_shares = active_target[t] * pre_trade_equity / val_px
        delta = target_shares - prev_shares

        cash_t = cash[t - 1]
        for n in range(N):
            qty = float(delta[n])
            if abs(qty) < 1e-9:
                continue
            px = float(fill_px[n])
            notional = abs(qty) * px
            exec_px = px * (1.0 + half_spread) if qty > 0 else px * (1.0 - half_spread)
            commission = cm.commission_bps / 10_000.0 * notional
            slippage = cm.slippage_bps / 10_000.0 * notional
            spread_cost = half_spread * notional
            ends_short = (prev_shares[n] + qty) < -1e-9
            locate = cm.locate_bps / 10_000.0 * notional if (ends_short and cm.locate_bps) else 0.0
            cash_t += -qty * exec_px
            cash_t -= commission + slippage + locate
            totals["commission"] += commission
            totals["slippage"] += slippage
            totals["spread"] += spread_cost
            totals["locate"] += locate
            trades.append(
                {
                    "date": dates[t],
                    "asset": assets[n],
                    "side": "buy" if qty > 0 else "sell_short",
                    "quantity": round(qty, 6),
                    "price": round(exec_px, 6),
                    "commission": round(commission, 6),
                    "slippage": round(slippage, 6),
                    "spread": round(spread_cost, 6),
                    "locate": round(locate, 6),
                }
            )
        # daily borrow on outstanding shorts (held into this bar)
        day_borrow = 0.0
        for n in range(N):
            if prev_shares[n] < -1e-9:
                short_mv = abs(prev_shares[n] * float(mark_prev[n]))
                b = cm.borrow_bps / 10_000.0 * short_mv / 252.0
                cash_t -= b
                totals["borrow"] += b
                day_borrow += b
        daily_borrow[t] = day_borrow
        cash[t] = cash_t
        shares[t] = target_shares
        prev_shares = target_shares
        equity[t] = cash[t] + float(np.sum(shares[t] * close[t]))

    # equity identity assertion
    for t in range(T):
        recon = cash[t] + float(np.sum(shares[t] * close[t]))
        if abs(recon - equity[t]) > 1e-4 * max(initial_capital, 1.0):
            raise AssertionError(f"accounting invariant violated at t={t}: {recon} != {equity[t]}")

    denom = np.where(equity > 1e-9, equity, initial_capital)
    gross_exp = np.abs(shares * close).sum(axis=1) / denom
    net_exp = (shares * close).sum(axis=1) / denom
    turnover = np.zeros(T, dtype=float)
    denom_prev = np.where(equity[:-1] > 1e-9, equity[:-1], initial_capital)
    turnover[1:] = np.sum(np.abs(shares[1:] - shares[:-1]) * close[:-1], axis=1) / denom_prev

    total_cost = sum(totals.values())
    cost_summary = {k: round(v, 4) for k, v in totals.items()}
    cost_summary["total"] = round(total_cost, 4)

    metrics = _compute_metrics(
        equity=equity,
        shares=shares,
        cap=float(initial_capital),
        benchmark_close=benchmark_close,
        cost_summary=cost_summary,
        gross_exp=gross_exp,
        net_exp=net_exp,
        turnover=turnover,
    )

    backtest_id = _stable_hash(
        {
            "strategy_hash": plan.strategy_hash,
            "dates": dates,
            "assets": assets,
            "equity_end": float(equity[-1]),
            "cost_total": total_cost,
        }
    )

    return BacktestResult(
        backtest_id=backtest_id,
        strategy_hash=plan.strategy_hash,
        dates=dates,
        assets=assets,
        target_weights=signal,
        executed_weights=active_target,
        shares=shares,
        equity_curve=equity,
        cash=cash,
        gross_exposure=gross_exp,
        net_exposure=net_exp,
        turnover=turnover,
        cost_summary=cost_summary,
        metrics=metrics,
        daily_borrow=daily_borrow,
        trades=trades,
        warnings=list(plan.warnings),
        provenance=provenance or dict(plan.provenance),
        benchmark_close=(np.asarray(benchmark_close, dtype=float) if benchmark_close is not None else None),
    )


def _compute_metrics(
    *,
    equity: np.ndarray,
    shares: np.ndarray,
    cap: float,
    benchmark_close: np.ndarray | None,
    cost_summary: dict[str, float],
    gross_exp: np.ndarray,
    net_exp: np.ndarray,
    turnover: np.ndarray,
) -> dict[str, Any]:
    rets = equity[1:] / equity[:-1] - 1.0
    n = len(rets)
    mean = float(np.mean(rets)) if n else 0.0
    std = float(np.std(rets, ddof=1)) if n > 1 else 0.0
    vol = std * math.sqrt(252.0)
    sharpe = (mean / std * math.sqrt(252.0)) if std > 0 else 0.0
    cagr = (equity[-1] / cap) ** (252.0 / max(n, 1)) - 1.0 if (n and equity[-1] > 0 and cap > 0) else 0.0
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    max_dd = float(np.min(dd)) if n else 0.0
    calmar = (cagr / abs(max_dd)) if max_dd < 0 else (cagr if cagr > 0 else 0.0)
    downside = rets[rets < 0]
    dvar = float(np.var(downside, ddof=1)) if len(downside) > 1 else 0.0
    sortino = (mean / math.sqrt(dvar) * math.sqrt(252.0)) if dvar > 0 else 0.0

    bench_cagr = bench_sharpe = None
    if benchmark_close is not None and len(benchmark_close) == len(equity):
        bc = np.asarray(benchmark_close, dtype=float)
        fin = np.where(np.isfinite(bc) & (bc > 0))[0]
        if len(fin) >= 2:
            b0, b1 = int(fin[0]), int(fin[-1])
            span = max(b1 - b0, 1)
            brets = bc[b0 + 1 : b1 + 1] / bc[b0:b1] - 1.0
            brets = brets[np.isfinite(brets)]
            bmean = float(np.mean(brets)) if len(brets) else 0.0
            bstd = float(np.std(brets, ddof=1)) if len(brets) > 1 else 0.0
            bench_cagr = (bc[b1] / bc[b0]) ** (252.0 / span) - 1.0
            bench_sharpe = (bmean / bstd * math.sqrt(252.0)) if bstd > 0 else 0.0

    avg_holdings = float(np.mean([np.sum(shares[t] != 0) for t in range(len(shares))]))
    return {
        "cumulative_return": round(float(equity[-1] / cap - 1.0), 6),
        "final_equity": round(float(equity[-1]), 2),
        "cagr": round(cagr, 6),
        "volatility": round(vol, 6),
        "sharpe": round(sharpe, 6),
        "sortino": round(sortino, 6),
        "calmar": round(calmar, 6),
        "max_drawdown": round(max_dd, 6),
        "benchmark_cagr": round(bench_cagr, 6) if bench_cagr is not None else None,
        "benchmark_sharpe": round(bench_sharpe, 6) if bench_sharpe is not None else None,
        "turnover_annualized_avg": round((float(np.mean(turnover[1:])) if n else 0.0) * 252.0, 6),
        "gross_exposure_avg": round(float(np.mean(gross_exp)), 6),
        "net_exposure_avg": round(float(np.mean(net_exp)), 6),
        "avg_holdings": round(avg_holdings, 4),
        "cost_total": cost_summary["total"],
        "cost_pct_of_capital": round((cost_summary["total"] / cap) if cap else 0.0, 6),
        **{k: cost_summary[k] for k in ("commission", "slippage", "spread", "borrow", "locate")},
    }


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


__all__ = ["CostModel", "BacktestResult", "run_accounting"]
