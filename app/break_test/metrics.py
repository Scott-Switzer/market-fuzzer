from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

import numpy as np

from app.break_test.costs import (
    almgren_chriss_impact_bps,
    borrow_fee_bps_for_short,
    toxicity_bps,
)


@dataclass(frozen=True)
class CostModelResult:
    spread_bps: float
    temporary_impact_bps: float
    permanent_impact_bps: float
    exchange_fee_bps: float
    borrow_cost_bps: float
    toxicity_bps: float = 0.0
    total_bps: float = 0.0


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _estimate_daily_vol(prices: np.ndarray, window: int = 20) -> np.ndarray:
    if len(prices) < 2:
        return np.full(1, 0.015, dtype=float)
    returns = np.diff(prices) / np.clip(prices[:-1], 1e-9, None)
    vol = np.full(len(prices), 0.015, dtype=float)
    if len(returns) >= window:
        vol[window:] = np.std(
            np.lib.stride_tricks.sliding_window_view(returns, window),
            axis=1,
            ddof=1,
        )
    elif len(returns) > 1:
        vol[-len(returns) :] = float(np.std(returns, ddof=1))
    vol = np.maximum(vol, 1e-9)
    return vol


def _impact_bps(
    relative_size: float,
    daily_vol: float,
    perm_eta: float = 0.05,
    temp_epsilon: float = 0.005,
    temp_gamma: float = 0.20,
) -> tuple[float, float]:
    return almgren_chriss_impact_bps(
        relative_size,
        daily_vol,
        perm_eta=perm_eta,
        temp_epsilon=temp_epsilon,
        temp_gamma=temp_gamma,
    )


def _spread_bps(daily_vol: float) -> float:
    return _clamp(daily_vol * 5_000.0, 0.0, 200.0)


def _borrow_fee_bps(
    short_position: float,
    price: float,
    adtv: float,
    locate_annual_bps: float,
    htb_annual_bps: float,
    holding_days: float = 1.0,
    htb_schedule: Optional[Sequence[dict]] = None,
) -> float:
    return borrow_fee_bps_for_short(
        short_shares=short_position,
        price=price,
        locate_fee_bps_annual=locate_annual_bps,
        htb_bps_annual=htb_annual_bps,
        htb_schedule=htb_schedule,
        holding_days=holding_days,
    )


def _tiered_fee_bps(
    notional_cents: int,
    schedule: Optional[Sequence[dict]] = None,
    default_fee_bps: float = 0.0,
) -> float:
    if not schedule:
        return float(default_fee_bps)
    rate = float(default_fee_bps)
    for tier in schedule:
        threshold = float(tier.get("threshold_cents", tier.get("threshold", 0.0)))
        tier_fee = float(tier.get("fee_bps", default_fee_bps))
        if notional_cents >= threshold:
            rate = tier_fee
        else:
            break
    return rate


def compute_turnover_cost(
    prices: np.ndarray,
    positions: np.ndarray,
    exchange_spec: Optional[object] = None,
    vol: Optional[Sequence[float]] = None,
    adtv_scaled: Optional[np.ndarray] = None,
    is_short: bool = False,
    *,
    signed_flow: Optional[Sequence[float]] = None,
    depth: Optional[Sequence[float]] = None,
) -> np.ndarray:
    """Return per-bar cost as a decimal fraction of price (bps / 10_000)."""
    px = np.asarray(prices, dtype=float)
    pos = np.asarray(positions, dtype=float)
    n = max(px.size, pos.size)
    if n < 2:
        return np.zeros(max(n - 1, 0), dtype=float)

    prices_trim = px[: n - 1]
    positions_trim = pos[: n - 1]
    trade_qty = np.diff(pos[:n], prepend=pos[0] if pos.size else 0.0)[: n - 1]

    if exchange_spec is None:
        return np.full(n - 1, 2.0 / 10_000, dtype=float)

    if vol is None:
        vol_arr = _estimate_daily_vol(px)[: n - 1]
    else:
        vol_arr = np.asarray(vol, dtype=float)
        if vol_arr.size != px.size:
            vol_arr = np.full(px.size, float(np.mean(vol_arr)) if vol_arr.size else 0.015, dtype=float)[
                : n - 1
            ]

    if adtv_scaled is None:
        adtv_arr = np.full(n - 1, float(getattr(exchange_spec, "adtv", 1)), dtype=float)
    else:
        adtv_arr = np.asarray(adtv_scaled, dtype=float)
        if adtv_arr.size != px.size:
            adtv_arr = np.full(px.size, float(np.mean(adtv_arr)) if adtv_arr.size else 1.0, dtype=float)[
                : n - 1
            ]

    taker_fee = float(getattr(exchange_spec, "taker_fee_bps", 0.3))
    perm_eta = float(getattr(exchange_spec, "perm_eta", 0.05))
    temp_epsilon = float(getattr(exchange_spec, "temp_epsilon", 0.005))
    temp_gamma = float(getattr(exchange_spec, "temp_gamma", 0.20))
    locate = float(getattr(exchange_spec, "locate_fee_bps_annual", 200.0))
    htb = float(getattr(exchange_spec, "htb_bps_annual", 0.0))
    htb_schedule = getattr(exchange_spec, "htb_schedule", None)
    toxicity_kappa = float(getattr(exchange_spec, "toxicity_kappa", 5.0))
    fee_schedule = getattr(exchange_spec, "fee_schedule", None)

    turnover = np.abs(trade_qty)
    spread_arr = np.array([_spread_bps(float(v)) for v in vol_arr], dtype=float)

    if fee_schedule:
        fee_arr = np.fromiter(
            (
                _tiered_fee_bps(int(round(notional)), fee_schedule, taker_fee)
                for notional in (turnover * prices_trim)
            ),
            dtype=float,
            count=len(turnover),
        )
    else:
        fee_arr = np.full(len(turnover), _tiered_fee_bps(0, None, taker_fee), dtype=float)

    impact_arr = np.zeros(len(turnover), dtype=float)
    for index, qty in enumerate(turnover):
        if qty <= 0:
            continue
        relative = float(qty) / max(float(adtv_arr[index]), 1.0)
        permanent, temporary = _impact_bps(
            relative,
            float(vol_arr[index]),
            perm_eta=perm_eta,
            temp_epsilon=temp_epsilon,
            temp_gamma=temp_gamma,
        )
        impact_arr[index] = permanent + temporary

    flow = np.asarray(signed_flow if signed_flow is not None else np.zeros(len(turnover)), dtype=float)
    depths = np.asarray(depth if depth is not None else np.zeros(len(turnover)), dtype=float)
    if flow.size != len(turnover):
        flow = np.resize(flow, len(turnover))
    if depths.size != len(turnover):
        depths = np.resize(depths, len(turnover))
    tox_arr = np.zeros(len(turnover), dtype=float)
    for index in range(len(turnover)):
        if index == 0:
            continue
        tox_arr[index] = toxicity_bps(float(flow[index - 1]), float(depths[index - 1]), kappa=toxicity_kappa)

    borrow_cost = np.zeros(len(turnover), dtype=float)
    short_inventory = np.maximum(-positions_trim, 0.0)
    if is_short or np.any(short_inventory > 0):
        for index, shares in enumerate(short_inventory):
            if shares <= 0:
                continue
            borrow_cost[index] = _borrow_fee_bps(
                short_position=float(shares),
                price=float(prices_trim[index]),
                adtv=float(adtv_arr[index]),
                locate_annual_bps=locate,
                htb_annual_bps=htb,
                htb_schedule=htb_schedule,
            )

    total_bps = spread_arr + fee_arr + impact_arr + tox_arr + borrow_cost
    return total_bps * 1e-4


def cost_for_trade(
    price: float,
    size: float,
    adtv: float,
    daily_vol: Optional[float] = None,
    *,
    locates_annual_bps: float = 200.0,
    htb_annual_bps: float = 0.0,
    maker_fee_bps: float = -0.1,
    taker_fee_bps: float = 0.3,
    maker_schedule: Optional[List[dict]] = None,
    taker_schedule: Optional[List[dict]] = None,
    htb_schedule: Optional[List[dict]] = None,
    holding_days: float = 1.0,
    side: str = "buy",
    signed_flow_prev: float = 0.0,
    depth_prev: float = 0.0,
    toxicity_kappa: float = 5.0,
    perm_eta: float = 0.05,
    temp_epsilon: float = 0.005,
    temp_gamma: float = 0.20,
) -> CostModelResult:
    rel_size = abs(float(size)) / max(float(adtv), 1.0)
    vol = 0.015 if daily_vol is None else float(daily_vol)

    perm_bps, temp_bps = _impact_bps(
        rel_size,
        vol,
        perm_eta=perm_eta,
        temp_epsilon=temp_epsilon,
        temp_gamma=temp_gamma,
    )

    notional_cents = int(round(abs(float(size)) * float(price) * 100))
    sched = taker_schedule if side == "sell" else maker_schedule
    fee = _tiered_fee_bps(notional_cents, sched, taker_fee_bps if side == "sell" else maker_fee_bps)

    borrow_bps = _borrow_fee_bps(
        short_position=abs(float(size)) if side == "sell" else 0.0,
        price=float(price),
        adtv=float(adtv),
        locate_annual_bps=float(locates_annual_bps),
        htb_annual_bps=float(htb_annual_bps),
        holding_days=float(holding_days),
        htb_schedule=htb_schedule,
    )

    tox = toxicity_bps(signed_flow_prev, depth_prev, kappa=toxicity_kappa)
    spread = _spread_bps(vol)
    total = spread + temp_bps + perm_bps + fee + borrow_bps + tox

    return CostModelResult(
        spread_bps=round(spread, 4),
        temporary_impact_bps=round(temp_bps, 4),
        permanent_impact_bps=round(perm_bps, 4),
        exchange_fee_bps=round(fee, 4),
        borrow_cost_bps=round(borrow_bps, 4),
        toxicity_bps=round(tox, 4),
        total_bps=round(total, 4),
    )


def compute_tca_metrics(
    *,
    arrival_price: float,
    average_execution_price: float,
    market_vwap: float,
    final_price: float,
    filled_quantity: float,
    target_quantity: float,
    side: str = "buy",
    completion_penalty_bps: float = 25.0,
) -> dict[str, float]:
    """Expanded TCA block shared by simulation summary and public metrics."""
    direction = 1.0 if side == "buy" else -1.0
    arrival = max(float(arrival_price), 1e-9)
    vwap = max(float(market_vwap), 1e-9)
    avg = float(average_execution_price)
    last = float(final_price)
    fill_rate = float(filled_quantity) / max(float(target_quantity), 1.0)
    fill_rate = _clamp(fill_rate, 0.0, 1.0)
    executed = filled_quantity > 0 and avg > 0

    slippage_vs_arrival = direction * (avg / arrival - 1.0) * 10_000.0 if executed else 0.0
    slippage_vs_vwap = direction * (avg / vwap - 1.0) * 10_000.0 if executed else 0.0
    opportunity_cost = direction * (last / arrival - 1.0) * 10_000.0 * (1.0 - fill_rate)
    completion_rate_penalty_bps = (1.0 - fill_rate) * float(completion_penalty_bps)

    return {
        "slippage_vs_arrival": round(float(slippage_vs_arrival), 4),
        "slippage_vs_vwap": round(float(slippage_vs_vwap), 4),
        "opportunity_cost": round(float(opportunity_cost), 4),
        "completion_rate_penalty_bps": round(float(completion_rate_penalty_bps), 4),
        "fill_rate": round(fill_rate, 6),
    }


def tca_by_bucket(
    trade_rows: Sequence[dict[str, Any]],
    *,
    arrival_price: float,
    side: str = "buy",
    bucket_edges: Sequence[float] = (0.0, 0.01, 0.05, 0.10, 0.25, 1.0),
    adtv: float = 1_000_000.0,
) -> list[dict[str, float | int | str]]:
    """Bucket TCA by participation rate (trade qty / ADTV)."""
    direction = 1.0 if side == "buy" else -1.0
    arrival = max(float(arrival_price), 1e-9)
    edges = list(bucket_edges)
    buckets: list[dict[str, Any]] = []
    for low, high in zip(edges[:-1], edges[1:]):
        buckets.append(
            {
                "bucket": f"{low:.2f}-{high:.2f}",
                "participation_low": float(low),
                "participation_high": float(high),
                "trades": 0,
                "quantity": 0,
                "notional_ticks": 0.0,
                "slippage_vs_arrival_sum": 0.0,
            }
        )

    for row in trade_rows:
        qty = abs(float(row.get("quantity", 0) or 0))
        px = float(row.get("price_ticks", row.get("price", 0)) or 0)
        if qty <= 0 or px <= 0:
            continue
        participation = qty / max(float(adtv), 1.0)
        target = None
        for bucket in buckets:
            if bucket["participation_low"] <= participation < bucket["participation_high"] or (
                participation >= bucket["participation_high"] and bucket is buckets[-1]
            ):
                target = bucket
                break
        if target is None:
            continue
        slip = direction * (px / arrival - 1.0) * 10_000.0
        target["trades"] += 1
        target["quantity"] += int(qty)
        target["notional_ticks"] += px * qty
        target["slippage_vs_arrival_sum"] += slip * qty

    out: list[dict[str, float | int | str]] = []
    for bucket in buckets:
        qty = int(bucket["quantity"])
        avg_slip = float(bucket["slippage_vs_arrival_sum"]) / qty if qty > 0 else 0.0
        avg_px = float(bucket["notional_ticks"]) / qty if qty > 0 else 0.0
        out.append(
            {
                "bucket": str(bucket["bucket"]),
                "trades": int(bucket["trades"]),
                "quantity": qty,
                "avg_price_ticks": round(avg_px, 4),
                "slippage_vs_arrival": round(avg_slip, 4),
            }
        )
    return out


def backtest_metrics(
    prices: np.ndarray,
    positions: np.ndarray,
    fee_bps: float = 2.0,
    *,
    exchange_spec: object | None = None,
    tcost_model: object | None = None,
    default_adv: float | None = None,
    signed_flow: Sequence[float] | None = None,
    depth: Sequence[float] | None = None,
    side: str = "buy",
    arrival_price: float | None = None,
    average_execution_price: float | None = None,
    market_vwap: float | None = None,
    filled_quantity: float | None = None,
    target_quantity: float | None = None,
    strategy_trades: Sequence[dict[str, Any]] | None = None,
) -> dict[str, float | int | list]:
    px = np.asarray(prices, dtype=float)
    pos = np.asarray(positions, dtype=float)
    returns = np.diff(px) / px[:-1]
    held = pos[:-1]
    if tcost_model is not None:
        if hasattr(tcost_model, "costs_for_signals"):
            costs_bps = np.asarray(
                tcost_model.costs_for_signals(
                    px,
                    pos,
                    default_adv=default_adv,
                    signed_flow=signed_flow,
                    depth=depth,
                ),
                dtype=float,
            )
            costs = costs_bps / 10_000.0
        else:
            costs = np.asarray(
                np.fromiter(
                    (
                        float(
                            tcost_model.trade_cost_bps(
                                px[i],
                                pos[i],
                                side=int(np.sign(pos[i])) if i > 0 else 1,
                                current_inventory=pos[i - 1],
                                default_adv=default_adv,
                            )
                            / 10_000.0
                        )
                        for i in range(len(returns))
                    ),
                    dtype=float,
                    count=len(returns),
                )
            )
    else:
        costs = compute_turnover_cost(
            px,
            pos,
            exchange_spec=exchange_spec,
            signed_flow=signed_flow,
            depth=depth,
        )
    if costs.size != returns.size:
        costs = np.resize(costs, returns.size)
    strategy_returns = held * returns - costs
    equity = np.cumprod(1 + strategy_returns)
    trades = int(np.sum(np.diff(pos, prepend=0.0) > 0))
    entries = np.flatnonzero(np.diff(pos, prepend=0.0) > 0)
    exits = np.flatnonzero(np.diff(pos, append=0.0) < 0)
    trade_returns = [
        float(px[exit_] / px[entry] - 1)
        for entry, exit_ in zip(entries, exits)
        if exit_ > entry and px[entry] > 0
    ]
    turnover = float(np.sum(np.abs(np.diff(pos, prepend=0.0))[:-1])) if len(pos) > 1 else 0.0
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

    win_rate = sum(1 for r in trade_returns if r > 0) / len(trade_returns) * 100 if trade_returns else 0.0
    wins = [r for r in trade_returns if r > 0]
    losses = [r for r in trade_returns if r < 0]
    profit_factor = float(sum(wins) / abs(sum(losses))) if wins and losses else 0.0

    var_95 = float(np.percentile(strategy_returns, 5)) if len(strategy_returns) else 0.0
    cvar_95 = float(np.mean(strategy_returns[strategy_returns <= var_95])) if len(strategy_returns) else 0.0

    arrival = float(arrival_price) if arrival_price is not None else float(px[0])
    avg_px = float(average_execution_price) if average_execution_price is not None else arrival
    vwap = float(market_vwap) if market_vwap is not None else float(np.mean(px))
    filled = (
        float(filled_quantity)
        if filled_quantity is not None
        else float(np.sum(np.abs(np.diff(pos, prepend=0.0))))
    )
    target = float(target_quantity) if target_quantity is not None else max(filled, 1.0)
    tca = compute_tca_metrics(
        arrival_price=arrival,
        average_execution_price=avg_px,
        market_vwap=vwap,
        final_price=float(px[-1]),
        filled_quantity=filled,
        target_quantity=target,
        side=side,
    )
    adtv = float(getattr(exchange_spec, "adtv", default_adv or 1_000_000.0) or 1_000_000.0)
    buckets = tca_by_bucket(
        strategy_trades or [],
        arrival_price=arrival,
        side=side,
        adtv=adtv,
    )

    return {
        "total_return_pct": round((float(equity[-1]) - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_dd_duration_days": round(max_dd_duration, 1),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "calmar": round(calmar, 2),
        "trades": trades,
        "turnover": round(turnover, 2),
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
        "slippage_vs_vwap": tca["slippage_vs_vwap"],
        "slippage_vs_arrival": tca["slippage_vs_arrival"],
        "opportunity_cost": tca["opportunity_cost"],
        "completion_rate_penalty_bps": tca["completion_rate_penalty_bps"],
        "tca_by_bucket": buckets,
    }


def compute_equity_curve(
    prices: np.ndarray,
    positions: np.ndarray,
    fee_bps: float = 2.0,
    *,
    exchange_spec: object | None = None,
    tcost_model: object | None = None,
    default_adv: float | None = None,
) -> list[float]:
    px = np.asarray(prices, dtype=float)
    pos = np.asarray(positions, dtype=float)
    returns = np.diff(px) / px[:-1]
    held = pos[:-1]
    if tcost_model is not None:
        if hasattr(tcost_model, "costs_for_signals"):
            costs = (
                np.asarray(tcost_model.costs_for_signals(px, pos, default_adv=default_adv), dtype=float)
                / 10_000.0
            )
        else:
            costs = np.asarray(
                np.fromiter(
                    (
                        float(
                            tcost_model.trade_cost_bps(
                                px[i],
                                pos[i],
                                side=int(np.sign(pos[i])) if i > 0 else 1,
                                current_inventory=pos[i - 1],
                                default_adv=default_adv,
                            )
                            / 10_000.0
                        )
                        for i in range(len(returns))
                    ),
                    dtype=float,
                    count=len(returns),
                )
            )
    else:
        costs = compute_turnover_cost(px, pos, exchange_spec=exchange_spec)
    if costs.size != returns.size:
        costs = np.resize(costs, returns.size)
    strategy_returns = held * returns - costs
    equity = np.cumprod(1 + strategy_returns)
    return [round(float(v), 6) for v in equity]
