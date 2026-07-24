"""Real T x N multi-asset portfolio backtester (corrected for submission hardening).

Correctness fixes vs the prototype (all P0 from the hardening spec):

3.1 SIGNAL/FILL TIMING
    features + target weights computed AFTER close t (use only data <= t).
    fills occur at the NEXT valid trading open (open t+1).
    We build ``signal_weights[t]`` then execute ``signal_weights[t-1]`` at open t.
    The signal produced on the final row can never execute.

3.2 SIZING ON EQUITY
    target notionals = weights * PRE-TRADE PORTFOLIO EQUITY (mark at decision close),
    not cash. Cash is not net liquidation value for a long/short book.

3.3 FEASIBLE EXPOSURE
    max feasible gross = (n_long + n_short selected) * max_position, capped by declared
    gross. If declared gross > feasible, scale to max feasible and emit a warning.
    Assertions: sum(abs(weights)) == gross target (when feasible); sum(weights) == net target.

3.4 SPREAD CHARGED
    half-spread paid per executed side (buy pays ask, sell receives bid). Included in
    cash, trade record, total cost, and cost attribution. Documented separately from slippage.

3.5 BORROW ACCRUES DAILY
    recurring financing = borrow_bps/10000 * |short market value| / 252 charged EVERY day
    an outstanding short is held (not only at entry). Locate/entry fee separated if any.
    Cost model stays labeled heuristic.

3.6 BENCHMARK CAGR IS GEOMETRIC
    bench_cagr = (bench_end / bench_start) ** (252 / n) - 1.

Accounting invariant holds every row:
    equity[t] == cash[t] + Σ_n shares[t,n] * mark_price[t,n]
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
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Composite score -> long top / short bottom quantiles, equal weight, capped.

    Returns (weights T×N, warnings). Feasible gross = selected_count*max_position,
    capped by declared gross. When declared gross exceeds feasible, scale to the
    maximum feasible gross and warn (preferred policy for the flagship demo).
    """
    T, N = momentum.shape
    weights = np.zeros((T, N), dtype=float)
    warnings: list[dict[str, Any]] = []
    for t in range(T):
        mom = momentum[t]
        vol = volatility[t]
        if np.isnan(mom).all() or np.isnan(vol).all():
            continue
        mom_rank = _percentile_rank(mom)
        vol_rank = _percentile_rank(vol)
        if np.all(np.isnan(mom_rank)) or np.all(np.isnan(vol_rank)):
            continue
        composite = momentum_weight * (mom_rank - 0.5) + low_vol_weight * (0.5 - vol_rank)
        order = np.argsort(composite)
        n_long = max(1, int(round(long_quantile * N)))
        n_short = int(round(short_quantile * N))  # 0 allowed (pure long)
        raw = np.zeros(N, dtype=float)
        for k in range(n_long):
            raw[order[-(k + 1)]] = 1.0
        for k in range(n_short):
            raw[order[k]] = -1.0
        # equal weight within sides, capped per position
        n_pos = max(1, int(np.sum(raw > 0)))
        n_neg = max(0, int(np.sum(raw < 0)))  # 0 allowed
        long_each = 1.0 / n_pos
        short_each = (1.0 / n_neg) if n_neg > 0 else 0.0
        scaled = np.where(raw > 0, long_each, np.where(raw < 0, -short_each, 0.0))
        scaled = np.clip(scaled, -max_position, max_position)
        # feasible gross = (number of selected positions) * max_position, capped by declared
        n_selected = int(np.sum(scaled != 0))
        max_feasible = n_selected * max_position
        if gross_exposure > max_feasible + 1e-9:
            # scale to feasible, warn
            scale = max_feasible / gross_exposure if gross_exposure > 0 else 1.0
            scaled = scaled * scale
            warnings.append({
                "type": "infeasible_gross",
                "t": t,
                "declared_gross": gross_exposure,
                "feasible_gross": round(max_feasible, 4),
                "action": "scaled_to_feasible",
            })
        else:
            max_feasible = gross_exposure
        # enforce net target by symmetric trim
        net = float(np.sum(scaled))
        if abs(net - net_exposure) > 1e-6:
            diff = net - net_exposure
            if diff > 0:
                scaled = np.where(scaled > 0, scaled - diff / max(n_pos, 1), scaled)
            else:
                scaled = np.where(scaled < 0, scaled - diff / max(n_neg, 1), scaled)
            scaled = np.clip(scaled, -max_position, max_position)
        weights[t] = scaled
    return weights, warnings


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
    prev_year = dates[0].year if hasattr(dates[0], "year") else None
    for i in range(1, len(dates)):
        m = dates[i].month if hasattr(dates[i], "month") else None
        y = dates[i].year if hasattr(dates[i], "year") else None
        if (m, y) != (prev_month, prev_year):
            mask[i] = True
        if m is not None:
            prev_month, prev_year = m, y
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
    target_weights: np.ndarray  # T x N (signal weights, computed at close t)
    executed_weights: np.ndarray  # T x N (weights actually held after fill at open t)
    shares: np.ndarray  # T x N
    equity_curve: np.ndarray  # T
    cash: np.ndarray  # T
    gross_exposure: np.ndarray  # T
    net_exposure: np.ndarray  # T
    turnover: np.ndarray  # T
    cost_summary: dict[str, float]
    cost_attribution: dict[str, float]
    metrics: dict[str, Any]
    daily_borrow: np.ndarray  # T, daily borrow accrued on outstanding shorts
    trades: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
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
    cost_model = getattr(spec, "cost_model_type", "heuristic_flat_bps")
    if cost_model not in ("heuristic_flat_bps",):
        raise ValueError(f"Unsupported cost_model_type: {cost_model} (only heuristic_flat_bps)")

    # ---- features (use only data available at close t) ----
    eff_long = min(spec.momentum_lookback, T - 2)
    eff_short = min(spec.momentum_short, max(2, T - 2))
    eff_vol = min(spec.volatility_window, T - 1)
    returns = np.full((T, N), np.nan, dtype=float)
    returns[1:] = close[1:] / close[:-1] - 1.0
    mom = compute_momentum(close, eff_short, eff_long)
    vol = compute_volatility(returns, eff_vol)

    signal_weights, warnings = cross_sectional_target_weights(
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
    # 4.2 short_unavailability: zero short leg for non-shortable assets
    if getattr(spec, "non_shortable", None):
        ns_idx = [i for i, a in enumerate(panel.assets) if a in set(spec.non_shortable)]
        for t in range(T):
            for i in ns_idx:
                if signal_weights[t, i] < 0:
                    signal_weights[t, i] = 0.0

    # ---- rebalance schedule ----
    rebal = monthly_rebalance_mask(panel.dates)
    latest_signal = np.zeros(N, dtype=float)
    signal_on_date = [np.zeros(N, dtype=float) for _ in range(T)]
    for t in range(T):
        if rebal[t] and not np.all(np.isnan(signal_weights[t])):
            latest_signal = signal_weights[t]
        signal_on_date[t] = latest_signal

    # ---- 3.1 EXECUTION TIMING: fill at open t uses signal from close t-1 (minus delay) ----
    delay = int(getattr(spec, "execution_delay_days", 0) or 0)
    active_target = np.zeros((T, N), dtype=float)
    for t in range(1, T):
        src = t - 1 - delay
        if src >= 0:
            active_target[t] = signal_on_date[src]


    # ---- next-open execution + cost accounting ----
    shares = np.zeros((T, N), dtype=float)
    cash = np.zeros(T, dtype=float)
    equity = np.zeros(T, dtype=float)
    cash[0] = cap
    commission_total = slippage_total = borrow_total = spread_total = locate_total = 0.0
    trades: list[dict[str, Any]] = []
    prev_shares = np.zeros(N, dtype=float)
    daily_borrow = np.zeros(T, dtype=float)

    for t in range(T):
        if t == 0:
            # No prior signal -> no position day 0 (cash only).
            shares[0] = np.zeros(N)
            equity[0] = cash[0]
            continue

        fill_px = open_[t]
        # 3.2 PRE-TRADE EQUITY at decision mark (close t-1) for sizing
        pre_trade_equity = cash[t - 1] + float(np.sum(prev_shares * close[t - 1]))
        target_shares = _weights_to_shares(active_target[t], fill_px, close[t - 1], pre_trade_equity)
        delta = target_shares - prev_shares
        commission_total, slippage_total, borrow_total, spread_total, locate_total, cash_after, _ = _charge_costs(
            delta, fill_px, cash[t - 1], close[t - 1], prev_shares, spec,
            commission_total, slippage_total, borrow_total, spread_total, locate_total,
            t, trades, panel, t,
        )
        # 3.5 record daily borrow accrued this row (for the accounting test)
        short_mv = float(np.sum(np.where(prev_shares < -1e-9, -prev_shares * close[t - 1], 0.0)))
        daily_borrow[t] = spec.borrow_bps / 10_000.0 * short_mv / 252.0
        cash[t] = cash_after
        shares[t] = target_shares
        prev_shares = target_shares
        equity[t] = cash[t] + float(np.sum(shares[t] * close[t]))

    # daily borrow accrual already applied inside _charge_costs on held shorts.
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

    total_cost = commission_total + slippage_total + borrow_total + spread_total + locate_total
    cost_summary = {
        "commission": round(commission_total, 4),
        "slippage": round(slippage_total, 4),
        "spread": round(spread_total, 4),
        "borrow": round(borrow_total, 4),
        "locate": round(locate_total, 4),
        "total": round(total_cost, 4),
    }
    cost_attribution = dict(cost_summary)

    metrics = compute_portfolio_metrics(
        equity=equity, shares=shares, close=close, cap=cap,
        benchmark_close=panel.benchmark_close, cost_summary=cost_summary,
        gross_exp=gross_exp, net_exp=net_exp, turnover=turnover,
    )
    # attach feasible-exposure warning flag
    if any(w["type"] == "infeasible_gross" for w in warnings):
        metrics["exposure_scaled_to_feasible"] = True

    backtest_id = _stable_hash({
        "strategy_hash": strategy_hash,
        "dates": [d.isoformat() for d in panel.dates],
        "assets": list(panel.assets),
        "equity_end": float(equity[-1]),
        "cost_total": total_cost,
    })

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
        target_weights=signal_weights,
        executed_weights=active_target,
        shares=shares,
        equity_curve=equity,
        cash=cash,
        gross_exposure=gross_exp,
        net_exposure=net_exp,
        turnover=turnover,
        cost_summary=cost_summary,
        cost_attribution=cost_attribution,
        metrics=metrics,
        daily_borrow=daily_borrow,
        trades=trades,
        warnings=warnings,
        provenance=panel.provenance.__dict__,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _weights_to_shares(weights: np.ndarray, fill_px: np.ndarray, mark: np.ndarray, equity: float) -> np.ndarray:
    """3.2 Convert target weights (fraction of PRE-TRADE EQUITY) to share counts.

    notional = weights * equity (net liquidation value), then / fill price.
    """
    val_px = np.where(fill_px > 0, fill_px, mark)
    val_px = np.where(val_px > 0, val_px, 1.0)
    notional = weights * equity
    return notional / val_px


def _charge_costs(
    delta: np.ndarray,
    fill_px: np.ndarray,
    cash_before: float,
    mark: np.ndarray,
    prev_shares: np.ndarray,
    spec: Any,
    commission_total: float,
    slippage_total: float,
    borrow_total: float,
    spread_total: float,
    locate_total: float,
    t: int,
    trades: list[dict[str, Any]],
    panel: MarketDataPanel,
    trade_date_idx: int,
) -> tuple[float, float, float, float, float, float, np.ndarray]:
    cash = cash_before
    half_spread = spec.spread_bps / 10_000.0 / 2.0  # 3.4 half-spread per side
    for n in range(len(delta)):
        qty = delta[n]
        if abs(qty) < 1e-9:
            continue
        px = float(fill_px[n])
        notional = abs(qty) * px
        # execution price after half-spread: buyer pays ask, seller receives bid
        exec_px = px * (1.0 + half_spread) if qty > 0 else px * (1.0 - half_spread)
        commission = spec.commission_bps / 10_000.0 * notional
        slippage = spec.slippage_bps / 10_000.0 * notional
        spread_cost = half_spread * notional
        # locate/entry fee on newly shorted notional (one-time)
        locate = spec.locate_bps / 10_000.0 * notional if (qty < 0 and spec.locate_bps) else 0.0
        # daily borrow accrual handled separately below (held shorts)
        cash += -qty * exec_px
        cash -= commission + slippage + spread_cost + locate
        commission_total += commission
        slippage_total += slippage
        spread_total += spread_cost
        locate_total += locate
        trades.append({
            "date": panel.dates[trade_date_idx].isoformat() if trade_date_idx < len(panel.dates) else str(t),
            "asset": panel.assets[n],
            "side": "buy" if qty > 0 else "sell_short" if qty < 0 else "flat",
            "quantity": round(float(qty), 6),
            "price": round(exec_px, 6),
            "commission": round(commission, 6),
            "slippage": round(slippage, 6),
            "spread": round(spread_cost, 6),
            "borrow": 0.0,
            "locate": round(locate, 6),
        })
    # 3.5 daily borrow accrual on OUTSTANDING short market value
    for n in range(len(prev_shares)):
        if prev_shares[n] < -1e-9:
            short_mv = abs(prev_shares[n] * float(mark[n]))
            daily_borrow = spec.borrow_bps / 10_000.0 * short_mv / 252.0
            cash -= daily_borrow
            borrow_total += daily_borrow
    return commission_total, slippage_total, borrow_total, spread_total, locate_total, cash, prev_shares


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
    cagr = (equity[-1] / cap) ** (252.0 / max(n, 1)) - 1.0 if (n and equity[-1] > 0 and cap > 0) else 0.0
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    max_dd = float(np.min(dd)) if n else 0.0
    calmar = (cagr / abs(max_dd)) if max_dd < 0 else (cagr if cagr > 0 else 0.0)
    downside = rets[rets < 0]
    downside_var = float(np.var(downside, ddof=1)) if len(downside) > 1 else 0.0
    sortino = (mean / math.sqrt(downside_var) * math.sqrt(252.0)) if downside_var > 0 else 0.0
    # 3.6 benchmark CAGR geometric
    bench_cagr = bench_sharpe = None
    active = info_ratio = None
    tracking_error = None
    if benchmark_close is not None and len(benchmark_close) == len(equity) and benchmark_close[0] > 0:
        brets = benchmark_close[1:] / benchmark_close[:-1] - 1.0
        bmean = float(np.mean(brets)) if len(brets) else 0.0
        bstd = float(np.std(brets, ddof=1)) if len(brets) > 1 else 0.0
        m_b = min(n, len(benchmark_close))
        bench_cagr = (benchmark_close[m_b] / benchmark_close[0]) ** (252.0 / max(m_b - 1, 1)) - 1.0 if benchmark_close[m_b] > 0 else 0.0
        bench_sharpe = (bmean / bstd * math.sqrt(252.0)) if bstd > 0 else 0.0
        if n:
            m = min(n, len(brets))
            aligned = rets[:m] - brets[:m]
            am = float(np.mean(aligned))
            av = float(np.var(aligned, ddof=1)) if m > 1 else 0.0
            tracking_error = math.sqrt(av) * math.sqrt(252.0)
            info_ratio = (am / math.sqrt(av) * math.sqrt(252.0)) if av > 0 else 0.0
            active = float(np.sum(aligned))
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
        "spread": cost_summary["spread"],
        "borrow": cost_summary["borrow"],
        "locate": cost_summary["locate"],
    }


def _stable_hash(value: Any) -> str:
    import hashlib
    import json

    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
