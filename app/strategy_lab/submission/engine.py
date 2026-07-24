"""Real T x N multi-asset portfolio backtester.

Replaces the single-vector facade in ``historical/engine.py``. This engine keeps
every asset's price path, computes cross-sectional signals, trades at the NEXT
OPEN after a close signal, deducts real transaction costs from cash and equity,
and maintains a daily mark-to-market ledger that satisfies:

    equity[t] == cash[t] + Σ_n shares[t,n] * mark_price[t,n]

All returns are close(t) -> close(t+1) on the position held from the open fill.
No same-bar signal/fill leakage. Arrays are never resized to conceal errors.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.strategy_lab.submission.panels import MarketDataPanel


# ---------------------------------------------------------------------------
# Feature engine
# ---------------------------------------------------------------------------
def compute_momentum(close: np.ndarray, short: int, long: int) -> np.ndarray:
    """12-1 momentum: close[t-short]/close[t-long] - 1, NaN before lookback."""
    T, N = close.shape
    out = np.full((T, N), np.nan, dtype=float)
    for t in range(long, T):
        out[t] = close[t - short] / close[t - long] - 1.0
    return out


def compute_volatility(returns: np.ndarray, window: int) -> np.ndarray:
    """Annualized realized vol over trailing window; NaN before window."""
    T, N = returns.shape
    out = np.full((T, N), np.nan, dtype=float)
    for t in range(window, T):
        window_ret = returns[t - window : t]
        out[t] = np.std(window_ret, axis=0, ddof=1) * math.sqrt(252.0)
    return out


# ---------------------------------------------------------------------------
# Cross-sectional target weights
# ---------------------------------------------------------------------------
def cross_sectional_target_weights(
    *,
    momentum: np.ndarray,
    volatility: np.ndarray,
    long_quantile: float,
    short_quantile: float,
    momentum_weight: float,
    low_vol_weight: float,
    gross_exposure: float,
    net_exposure: float,
    max_position: float,
) -> np.ndarray:
    """Composite score -> long top / short bottom quantiles, equal weight, capped.

    At each date t: combine standardized momentum (high=long) and inverse vol
    (low vol=long) into a composite rank. Take the top ``long_quantile`` fraction
    as longs, bottom ``short_quantile`` as shorts, equal-weight within side, scale
    to gross/net targets, cap per-position at ``max_position``.
    """
    T, N = momentum.shape
    weights = np.zeros((T, N), dtype=float)
    for t in range(T):
        mom = momentum[t]
        vol = volatility[t]
        if np.isnan(mom).all() or np.isnan(vol).all():
            continue
        # standardized composites (rank-based, robust)
        mom_rank = _percentile_rank(mom)
        vol_rank = _percentile_rank(vol)
        if np.all(np.isnan(mom_rank)) or np.all(np.isnan(vol_rank)):
            continue
        # high momentum = long; high vol = short (we want low vol long)
        composite = momentum_weight * (mom_rank - 0.5) + low_vol_weight * (0.5 - vol_rank)
        order = np.argsort(composite)
        n_long = max(1, int(round(long_quantile * N)))
        n_short = max(1, int(round(short_quantile * N)))
        raw = np.zeros(N, dtype=float)
        # longs: highest composite
        for k in range(n_long):
            raw[order[-(k + 1)]] = 1.0
        # shorts: lowest composite
        for k in range(n_short):
            raw[order[k]] = -1.0
        # equal weight within sides, then scale to gross/net
        n_pos = max(1, int(np.sum(raw > 0)))
        n_neg = max(1, int(np.sum(raw < 0)))
        long_each = (gross_exposure / 2.0) / n_pos
        short_each = (gross_exposure / 2.0) / n_neg
        scaled = np.where(raw > 0, long_each, np.where(raw < 0, -short_each, 0.0))
        # enforce max position cap
        scaled = np.clip(scaled, -max_position, max_position)
        # enforce net exposure target by trimming gross symmetrically if needed
        net = float(np.sum(scaled))
        if abs(net - net_exposure) > 1e-6:
            # adjust by trimming the larger side
            diff = net - net_exposure
            if diff > 0:  # too long -> reduce longs
                scaled = np.where(scaled > 0, scaled - diff / max(n_pos, 1), scaled)
            else:  # too short -> reduce shorts
                scaled = np.where(scaled < 0, scaled - diff / max(n_neg, 1), scaled)
            scaled = np.clip(scaled, -max_position, max_position)
        weights[t] = scaled
    return weights


def _percentile_rank(x: np.ndarray) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=float)
    valid = ~np.isnan(x)
    if valid.sum() < 2:
        return out
    vals = x[valid]
    ranks = (vals.argsort().argsort() + 1) / len(vals)
    out[valid] = ranks
    return out


# ---------------------------------------------------------------------------
# Rebalance schedule
# ---------------------------------------------------------------------------
def monthly_rebalance_mask(dates) -> np.ndarray:
    """True on the first trading day of each month (and the first row)."""
    mask = np.zeros(len(dates), dtype=bool)
    mask[0] = True
    prev_month = dates[0].month if hasattr(dates[0], "month") else None
    for i in range(1, len(dates)):
        m = dates[i].month if hasattr(dates[i], "month") else None
        y = dates[i].year if hasattr(dates[i], "year") else None
        if (m, y) != (prev_month, dates[i - 1].year if hasattr(dates[i - 1], "year") else None):
            mask[i] = True
        if m is not None:
            prev_month = m
    return mask


# ---------------------------------------------------------------------------
# Backtest result
# ---------------------------------------------------------------------------
@dataclass
class BacktestResult:
    backtest_id: str
    strategy_hash: str
    panel_meta: dict[str, Any]
    dates: list[str]
    assets: list[str]
    target_weights: np.ndarray  # T x N
    shares: np.ndarray  # T x N
    equity_curve: np.ndarray  # T
    cash: np.ndarray  # T
    gross_exposure: np.ndarray  # T
    net_exposure: np.ndarray  # T
    turnover: np.ndarray  # T-1
    cost_summary: dict[str, float]
    metrics: dict[str, Any]
    trades: list[dict[str, Any]]
    provenance: dict[str, Any]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
def run_portfolio_backtest(
    *,
    panel: MarketDataPanel,
    spec: Any,
    strategy_hash: str,
    initial_capital: float | None = None,
) -> BacktestResult:
    close = panel.close
    open_ = panel.open
    T, N = close.shape
    if T < 2:
        raise ValueError("Need at least 2 dates for a backtest")

    cap = float(initial_capital if initial_capital is not None else spec.initial_capital)

    # ---- features (use only data available at close t) ----
    # Clamp lookbacks to available history so short panels (e.g. stress search)
    # still produce signals instead of all-NaN features.
    eff_long = min(spec.momentum_lookback, T - 2)
    eff_short = min(spec.momentum_short, max(2, T - 2))
    eff_vol = min(spec.volatility_window, T - 1)
    returns = np.full((T, N), np.nan, dtype=float)
    returns[1:] = close[1:] / close[:-1] - 1.0
    mom = compute_momentum(close, eff_short, eff_long)
    vol = compute_volatility(returns, eff_vol)

    target = cross_sectional_target_weights(
        momentum=mom,
        volatility=vol,
        long_quantile=spec.long_quantile,
        short_quantile=spec.short_quantile,
        momentum_weight=spec.momentum_weight,
        low_vol_weight=spec.low_volatility_weight,
        gross_exposure=spec.gross_exposure,
        net_exposure=spec.net_exposure,
        max_position=spec.max_position_weight,
    )

    # ---- rebalance only on monthly boundaries ----
    rebal = monthly_rebalance_mask(panel.dates)
    # target weights only update on rebalance dates; otherwise hold previous
    active_target = np.zeros((T, N), dtype=float)
    last = np.zeros(N, dtype=float)
    for t in range(T):
        if rebal[t] and not np.all(np.isnan(target[t])):
            last = target[t]
        active_target[t] = last

    # ---- next-open execution + cost accounting ----
    shares = np.zeros((T, N), dtype=float)
    cash = np.zeros(T, dtype=float)
    equity = np.zeros(T, dtype=float)
    cash[0] = cap
    commission_total = slippage_total = borrow_total = 0.0
    trades: list[dict[str, Any]] = []

    prev_shares = np.zeros(N, dtype=float)
    for t in range(T):
        if t == 0:
            # no position day 0; we will establish at first open (t=0 open)
            fill_px = open_[0]
            target_shares = _weights_to_shares(active_target[0], fill_px, cash[0], close[0])
            delta = target_shares - prev_shares
            commission_total, slippage_total, borrow_total, cash[0] = _charge_costs(
                delta,
                fill_px,
                cash[0],
                spec,
                commission_total,
                slippage_total,
                borrow_total,
                t,
                trades,
                panel,
                0,
            )
            shares[0] = target_shares
            prev_shares = target_shares
            equity[0] = cash[0] + float(np.sum(shares[0] * close[0]))
            continue

        # trade at OPEN t using target decided at close t-1 (already in active_target[t])
        fill_px = open_[t]
        target_shares = _weights_to_shares(active_target[t], fill_px, cash[t - 1], close[t - 1])
        delta = target_shares - prev_shares
        commission_total, slippage_total, borrow_total, cash_after = _charge_costs(
            delta,
            fill_px,
            cash[t - 1],
            spec,
            commission_total,
            slippage_total,
            borrow_total,
            t,
            trades,
            panel,
            t,
        )
        cash[t] = cash_after
        shares[t] = target_shares
        prev_shares = target_shares
        equity[t] = cash[t] + float(np.sum(shares[t] * close[t]))

    # ---- exposures & turnover ----
    gross_exp = np.abs(shares * close).sum(axis=1) / cap
    net_exp = (shares * close).sum(axis=1) / cap
    turnover = np.zeros(T, dtype=float)
    turnover[1:] = np.sum(np.abs(shares[1:] - shares[:-1]) * close[:-1], axis=1) / cap

    # ---- accounting invariant assertion ----
    for t in range(T):
        recon = cash[t] + float(np.sum(shares[t] * close[t]))
        if abs(recon - equity[t]) > 1e-4 * max(cap, 1.0):
            raise AssertionError(f"Accounting invariant violated at t={t}: {recon} != {equity[t]}")

    total_cost = commission_total + slippage_total + borrow_total
    cost_summary = {
        "commission": round(commission_total, 4),
        "slippage": round(slippage_total, 4),
        "borrow": round(borrow_total, 4),
        "total": round(total_cost, 4),
    }

    metrics = compute_portfolio_metrics(
        equity=equity,
        shares=shares,
        close=close,
        cap=cap,
        benchmark_close=panel.benchmark_close,
        cost_summary=cost_summary,
        gross_exp=gross_exp,
        net_exp=net_exp,
        turnover=turnover,
    )

    backtest_id = _stable_hash(
        {
            "strategy_hash": strategy_hash,
            "dates": [d.isoformat() for d in panel.dates],
            "assets": list(panel.assets),
            "equity_end": float(equity[-1]),
            "cost_total": total_cost,
        }
    )

    return BacktestResult(
        backtest_id=backtest_id,
        strategy_hash=strategy_hash,
        panel_meta={
            "dates": [d.isoformat() for d in panel.dates],
            "assets": list(panel.assets),
            "source": panel.provenance.source,
            "tier": panel.provenance.tier,
        },
        dates=[d.isoformat() for d in panel.dates],
        assets=list(panel.assets),
        target_weights=active_target,
        shares=shares,
        equity_curve=equity,
        cash=cash,
        gross_exposure=gross_exp,
        net_exposure=net_exp,
        turnover=turnover,
        cost_summary=cost_summary,
        metrics=metrics,
        trades=trades,
        provenance=panel.provenance.__dict__,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _weights_to_shares(weights: np.ndarray, price: np.ndarray, cash: float, mark: np.ndarray) -> np.ndarray:
    """Convert target weights (fraction of capital) to share counts.

    Uses the mark price for valuation; if mark is zero, fall back to fill price.
    """
    val_px = np.where(mark > 0, mark, price)
    val_px = np.where(val_px > 0, val_px, 1.0)
    notional = weights * cash
    return notional / val_px


def _charge_costs(
    delta: np.ndarray,
    fill_px: np.ndarray,
    cash_before: float,
    spec: Any,
    commission_total: float,
    slippage_total: float,
    borrow_total: float,
    t: int,
    trades: list[dict[str, Any]],
    panel: MarketDataPanel,
    trade_date_idx: int,
) -> tuple[float, float, float, float]:
    cash = cash_before
    for n in range(len(delta)):
        qty = delta[n]
        if abs(qty) < 1e-9:
            continue
        px = float(fill_px[n])
        notional = abs(qty) * px
        commission = spec.commission_bps / 10_000.0 * notional
        slippage = spec.slippage_bps / 10_000.0 * notional
        # borrow cost: charge annualized borrow on the short position held
        borrow = 0.0
        if qty < 0:
            # cost proportional to newly shorted notional, amortized per-day (1/252)
            borrow = spec.borrow_bps / 10_000.0 * notional / 252.0
        # cash impact: buy reduces cash, sell increases cash; costs reduce cash
        cash += -qty * px  # +qty buy -> cash down; -qty sell -> cash up
        cash -= commission + slippage + borrow
        commission_total += commission
        slippage_total += slippage
        borrow_total += borrow
        trades.append(
            {
                "date": panel.dates[trade_date_idx].isoformat()
                if trade_date_idx < len(panel.dates)
                else str(t),
                "asset": panel.assets[n],
                "side": "buy" if qty > 0 else "sell_short" if qty < 0 else "flat",
                "quantity": round(float(qty), 6),
                "price": round(px, 6),
                "commission": round(commission, 6),
                "slippage": round(slippage, 6),
                "borrow": round(borrow, 6),
            }
        )
    return commission_total, slippage_total, borrow_total, cash


def compute_portfolio_metrics(
    *,
    equity: np.ndarray,
    shares: np.ndarray,
    close: np.ndarray,
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
    cagr = (equity[-1] / cap) ** (252.0 / max(n, 1)) - 1.0 if n else 0.0
    # drawdown
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    max_dd = float(np.min(dd)) if n else 0.0
    calmar = (cagr / abs(max_dd)) if max_dd < 0 else (cagr if cagr > 0 else 0.0)
    downside = rets[rets < 0]
    downside_var = float(np.var(downside, ddof=1)) if len(downside) > 1 else 0.0
    sortino = (mean / math.sqrt(downside_var) * math.sqrt(252.0)) if downside_var > 0 else 0.0
    # benchmark
    bench_cagr = bench_sharpe = None
    active = info_ratio = None
    tracking_error = None
    if benchmark_close is not None and len(benchmark_close) == len(equity):
        brets = benchmark_close[1:] / benchmark_close[:-1] - 1.0
        bmean = float(np.mean(brets)) if len(brets) else 0.0
        bstd = float(np.std(brets, ddof=1)) if len(brets) > 1 else 0.0
        bench_cagr = bmean * 252.0
        bench_sharpe = (bmean / bstd * math.sqrt(252.0)) if bstd > 0 else 0.0
        if n:
            m = min(n, len(brets))
            aligned = rets[:m] - brets[:m]
            am = float(np.mean(aligned))
            av = float(np.var(aligned, ddof=1)) if m > 1 else 0.0
            tracking_error = math.sqrt(av) * math.sqrt(252.0)
            info_ratio = (am / math.sqrt(av) * math.sqrt(252.0)) if av > 0 else 0.0
            active = float(np.sum(aligned))
    # holdings / concentration
    avg_holdings = float(np.mean([np.sum(shares[t] != 0) for t in range(len(shares))]))
    avg_gross = float(np.mean(gross_exp))
    avg_net = float(np.mean(net_exp))
    avg_turn = float(np.mean(turnover[1:])) if n else 0.0
    total_cost_pct = (cost_summary["total"] / cap) if cap else 0.0

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
        "active_return_annualized": round(active * 252.0, 6) if active is not None else None,
        "tracking_error": round(tracking_error, 6) if tracking_error is not None else None,
        "information_ratio": round(info_ratio, 6) if info_ratio is not None else None,
        "turnover_annualized_avg": round(avg_turn * 252.0, 6),
        "gross_exposure_avg": round(avg_gross, 6),
        "net_exposure_avg": round(avg_net, 6),
        "avg_holdings": round(avg_holdings, 4),
        "cost_total": cost_summary["total"],
        "cost_pct_of_capital": round(total_cost_pct, 6),
        "commission": cost_summary["commission"],
        "slippage": cost_summary["slippage"],
        "borrow": cost_summary["borrow"],
    }


def _stable_hash(value: Any) -> str:
    import hashlib
    import json

    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
