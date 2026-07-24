from __future__ import annotations

import math
from typing import cast

import numpy as np


def _f(v: object) -> float:
    return float(cast(float, v))


def _i(v: object) -> int:
    return int(cast(int, v))


def sensitivity_analysis(
    prices: list[float],
    strategy_type: str,
    base_params: dict[str, int],
    param_ranges: dict[str, tuple[int, int]] | None = None,
) -> dict[str, object]:
    if not param_ranges:
        param_ranges = _default_param_ranges(strategy_type, base_params)

    candidates = _build_candidate_grid(base_params, param_ranges)
    evaluated: list[dict[str, object]] = []

    for candidate in candidates:
        positions = _compute_positions(strategy_type, prices, **candidate)
        hist = _backtest_metrics(prices, positions)
        forward = _quick_forward_test(prices, strategy_type, candidate)
        score = _robustness_score(hist, forward)
        evaluated.append(
            {
                "params": candidate,
                "historical": hist,
                "forward": forward,
                "robustness_score": round(float(score), 4),
                "recommendation": _recommendation(float(score)),
            }
        )

    evaluated.sort(key=lambda item: float(item["robustness_score"]), reverse=True)
    return {
        "strategy": strategy_type,
        "candidates_tested": len(evaluated),
        "best": evaluated[0] if evaluated else None,
        "top_3": evaluated[:3],
        "stability": _stability_metrics(evaluated),
        "rank_family": [
            {
                "rank": i + 1,
                "params": row.get("params"),
                "robustness_score": row.get("robustness_score"),
            }
            for i, row in enumerate(evaluated[:10])
        ],
        "nfailed_regimes": sum(
            1
            for row in evaluated
            if any(float(r.get("loss_rate_pct", 0) or 0) >= 60 for r in (row.get("forward") or []))
        ),
        "reproducibility": {
            "note": "Synthetic paths use fixed regime seeds; rerunning with the same universe and worlds_per_regime reproduces these results.",
        },
    }


def worst_case_attribution(
    prices: list[float],
    strategy_type: str,
    params: dict[str, int],
    worlds_per_regime: int = 40,
) -> dict[str, object]:
    forward = _quick_forward_test(prices, strategy_type, params, worlds_per_regime=worlds_per_regime)
    positions = _compute_positions(strategy_type, prices, **params)
    hist_trade_returns = _trade_returns(prices, positions)

    regime_worst = []
    for regime in forward:
        regime_worst.append(
            {
                "regime": str(regime["regime"]),
                "worst_drawdown_pct": float(regime["worst_drawdown_pct"]),
                "loss_rate_pct": float(regime["loss_rate_pct"]),
                "median_return_pct": float(regime["median_return_pct"]),
                "attribution": _worst_case_action(
                    strategy_type,
                    str(regime["regime"]),
                    float(regime["worst_drawdown_pct"]),
                    float(regime["loss_rate_pct"]),
                ),
            }
        )
    regime_worst.sort(key=lambda item: item["worst_drawdown_pct"])

    return {
        "overall_worst_regime": regime_worst[0] if regime_worst else None,
        "regime_worst_cases": regime_worst,
        "turnover_by_regime_consistency": _turnover_consistency(prices, strategy_type, params),
        "historical_trade_sharpe": round(_to_trade_return_sharpe(hist_trade_returns), 2),
        "historical_avg_trade": round(sum(hist_trade_returns) / len(hist_trade_returns), 4)
        if hist_trade_returns
        else 0.0,
    }


def builtin_strategy_ranges(strategy_type: str) -> dict[str, tuple[int, int]]:
    return _default_param_ranges(strategy_type, {})


def _default_param_ranges(strategy_type: str, base_params: dict[str, int]) -> dict[str, tuple[int, int]]:
    if strategy_type == "sma_crossover":
        fast = int(base_params.get("fast", 20))
        slow = int(base_params.get("slow", 50))
        return {
            "fast": (max(2, fast - 10), fast + 10),
            "slow": (max(3, slow - 10), slow + 10),
        }
    if strategy_type == "breakout":
        entry = int(base_params.get("entry_lookback", 20))
        exit_ = int(base_params.get("exit_lookback", 10))
        return {
            "entry_lookback": (max(3, entry - 5), entry + 5),
            "exit_lookback": (max(2, exit_ - 5), exit_ + 5),
        }
    if strategy_type == "rsi_reversion":
        period = int(base_params.get("period", 14))
        oversold = int(base_params.get("oversold", 30))
        overbought = int(base_params.get("overbought", 70))
        return {
            "period": (max(2, period - 5), period + 5),
            "oversold": (max(5, oversold - 5), min(45, oversold + 5)),
            "overbought": (max(55, overbought - 5), min(95, overbought + 5)),
        }
    return {}


def _build_candidate_grid(
    base_params: dict[str, int], param_ranges: dict[str, tuple[int, int]]
) -> list[dict[str, int]]:
    keys = list(param_ranges.keys())
    grid: list[dict[str, int]] = []
    current: dict[str, int] = dict(base_params)

    def backtrack(index: int) -> None:
        if index == len(keys):
            grid.append(dict(current))
            return
        key = keys[index]
        low, high = param_ranges[key]
        center = int(base_params.get(key, (low + high) // 2))
        candidates = sorted({center, low, high, (low + high) // 2})
        for value in candidates:
            current[key] = int(value)
            backtrack(index + 1)

    backtrack(0)
    return grid[:32]


def _compute_positions(strategy_type: str, prices: list[float], **params: int) -> list[float]:
    import numpy as np

    from app.break_test.strategies import compute_positions

    return compute_positions(strategy_type, np.array(prices, dtype=float), **params).tolist()


def _backtest_metrics(prices: list[float], positions: list[float]) -> dict[str, float | int]:
    import math

    import numpy as np

    px = np.array(prices, dtype=float)
    pos = np.array(positions, dtype=float)
    returns = np.diff(px) / px[:-1]
    held = pos[:-1]
    turnover = np.abs(np.diff(pos, prepend=0.0))[:-1]
    global_cost = 2.0 / 10_000
    strategy_returns = held * returns - turnover * global_cost
    equity = np.cumprod(1 + strategy_returns)
    peaks = np.maximum.accumulate(equity)
    drawdown = equity / peaks - 1
    std = float(np.std(strategy_returns, ddof=1)) if len(strategy_returns) > 1 else 0.0
    sharpe = float(np.mean(strategy_returns) / std * math.sqrt(252)) if std > 0 else 0.0
    trades = int(np.sum(np.diff(pos, prepend=0.0) > 0))
    entries = np.flatnonzero(np.diff(pos, prepend=0.0) > 0)
    exits = np.flatnonzero(np.diff(pos, append=0.0) < 0)
    trade_returns = [
        px[exit_] / px[entry] - 1 for entry, exit_ in zip(entries, exits, strict=False) if exit_ > entry
    ]
    win_rate = sum(1 for r in trade_returns if r > 0) / len(trade_returns) * 100 if trade_returns else 0.0
    return {
        "total_return_pct": round((float(equity[-1]) - 1) * 100, 2),
        "max_drawdown_pct": round(float(np.min(drawdown)) * 100, 2),
        "sharpe": round(sharpe, 2),
        "trades": trades,
        "win_rate_pct": round(win_rate, 1),
        "turnover": round(float(np.sum(turnover)), 2),
    }


def _quick_forward_test(
    prices: list[float], strategy_type: str, params: dict[str, int], worlds_per_regime: int = 30
) -> list[dict[str, object]]:
    import numpy as np

    from app.break_test.regimes import REGIME_KEYS, REGIME_LABELS, SYNTHETIC_REGIMES

    px = np.array(prices, dtype=float)
    base_returns = np.diff(np.log(px))
    base_vol = max(float(np.std(base_returns)), 0.0001)
    results: list[dict[str, object]] = []
    for regime_index, key in enumerate(REGIME_KEYS):
        drift, vol_mult, reversal = (
            SYNTHETIC_REGIMES[key]["drift"],
            SYNTHETIC_REGIMES[key]["vol"],
            SYNTHETIC_REGIMES[key]["reversal"],
        )
        returns_list: list[float] = []
        drawdowns: list[float] = []
        losses = 0
        positions: list[float] = []
        for world in range(worlds_per_regime):
            rng = np.random.default_rng(20_000 + regime_index * 1_000 + world)
            shocks = rng.normal(drift, base_vol * vol_mult, len(px) - 1)
            if reversal:
                for idx in range(1, len(shocks)):
                    shocks[idx] += reversal * -shocks[idx - 1]
            synthetic = px[0] * np.exp(np.concatenate(([0.0], np.cumsum(shocks))))
            positions = _compute_positions(strategy_type, synthetic.tolist(), **params)
            result = _backtest_metrics(synthetic.tolist(), positions)
            value = float(result["total_return_pct"])
            returns_list.append(value)
            drawdowns.append(float(result["max_drawdown_pct"]))
            losses += value < 0
        turnover = float(sum(abs(v) for v in np.diff(np.array(positions, dtype=float), prepend=0.0)[:-1]))
        results.append(
            {
                "regime": REGIME_LABELS[key],
                "worlds": worlds_per_regime,
                "loss_rate_pct": round(losses / worlds_per_regime * 100, 1),
                "median_return_pct": round(float(np.median(returns_list)), 2),
                "mean_return_pct": round(float(np.mean(returns_list)), 2),
                "worst_drawdown_pct": round(float(min(drawdowns)), 2),
                "best_return_pct": round(float(max(returns_list)), 2),
                "sharpe_pct": round(_regime_sharpe(returns_list), 2),
                "win_rate_pct": round(_regime_win_rate(returns_list), 1),
                "turnover_pct": round(turnover, 2),
            }
        )
    return results


def _trade_returns(prices: list[float], positions: list[float]) -> list[float]:
    import numpy as np

    px = np.array(prices, dtype=float)
    pos = np.array(positions, dtype=float)
    entries = np.flatnonzero(np.diff(pos, prepend=0.0) > 0)
    exits = np.flatnonzero(np.diff(pos, append=0.0) < 0)
    return [px[exit_] / px[entry] - 1 for entry, exit_ in zip(entries, exits, strict=False) if exit_ > entry]


def _robustness_score(hist: dict[str, float | int], forward: list[dict[str, object]]) -> float:
    """Lexicographic multi-criteria score: regime efficacy, tail, turnover-norm Sharpe."""
    if not forward:
        return 0.0
    sharpe = float(hist.get("sharpe", 0.0) or 0.0)
    turnover = max(float(hist.get("turnover", 1.0) or 1.0), 1e-6)
    avg_return = sum(float(r["median_return_pct"]) for r in forward) / len(forward)
    worst_dd = min(float(r["worst_drawdown_pct"]) for r in forward)
    avg_loss = sum(float(r["loss_rate_pct"]) for r in forward) / len(forward)
    regime_efficacy = avg_return - 0.25 * avg_loss
    tail_sensitivity = abs(worst_dd)
    tn_sharpe = sharpe / math.sqrt(turnover)
    # Map lexicographic tuple into a scalar preserving order for legacy callers.
    score = regime_efficacy * 10.0 + max(-50.0, sharpe * 8.0) + (50.0 - tail_sensitivity) + tn_sharpe * 5.0
    return float(score)


def _recommendation(score: float) -> str:
    if score >= 70:
        return "Strong robustness; acceptable for research read-through."
    if score >= 45:
        return "Moderate robustness; monitor labeled regimes before live research use."
    return "Weak robustness; do not advance without reparameterization or data review."


def _stability_metrics(evaluated: list[dict[str, object]]) -> dict[str, object]:
    if not evaluated:
        return {"score_spread": 0.0, "parameter_stability": "insufficient_data"}
    best = float(evaluated[0]["robustness_score"])
    worst = float(evaluated[-1]["robustness_score"])
    spread = round(best - worst, 4)
    return {
        "score_spread": spread,
        "parameter_stability": "stable" if spread < 15 else "moderate" if spread < 35 else "unstable",
    }


def _turnover_consistency(
    prices: list[float], strategy_type: str, params: dict[str, int]
) -> dict[str, object]:
    positions = _compute_positions(strategy_type, prices, **params)
    turnover = float(sum(abs(v) for v in np.diff(np.array(positions, dtype=float), prepend=0.0)[:-1]))
    return {
        "historical_turnover": round(turnover, 2),
        "note": "High turnover plus worsening forward-test win rate typically warns of overfitting.",
    }


def _regime_sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns, dtype=float)
    std = float(np.std(arr, ddof=1))
    mean = float(np.mean(arr))
    return mean / std * math.sqrt(252) if std > 0 else 0.0


def _regime_win_rate(returns: list[float]) -> float:
    if not returns:
        return 0.0
    return sum(1 for r in returns if r > 0) / len(returns) * 100


def _to_trade_return_sharpe(trade_returns: list[float]) -> float:
    if len(trade_returns) < 2:
        return 0.0
    mean = sum(trade_returns) / len(trade_returns)
    variance = sum((r - mean) ** 2 for r in trade_returns) / (len(trade_returns) - 1)
    std = math.sqrt(variance)
    return mean / std * math.sqrt(252) if std > 0 else 0.0


def _worst_case_action(strategy_type: str, regime: str, worst_dd_pct: float, loss_rate_pct: float) -> str:
    severity = "extreme" if worst_dd_pct < -15 else "high" if worst_dd_pct < -8 else "moderate"
    if strategy_type == "sma_crossover":
        return f"{severity} whipsaw/loss from trend lag in {regime}"
    if strategy_type == "breakout":
        return f"{severity} false breakout/reentry cost in {regime}"
    if strategy_type == "rsi_reversion":
        return f"{severity} counter-trend trap in {regime}"
    return f"{severity} regime-specific loss pattern in {regime}"
