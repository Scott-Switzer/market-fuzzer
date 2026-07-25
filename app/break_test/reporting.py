from __future__ import annotations

import json
from typing import cast


def _f(v: object) -> float:
    return float(cast(float, v))


def _i(v: object) -> int:
    return int(cast(int, v))


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 1:
        return float(sorted_vals[n // 2])
    return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0


def build_failure_report(
    strategy_type: str,
    params: dict[str, int],
    historical: dict[str, float | int],
    forward_results: list[dict[str, object]],
) -> dict[str, object]:
    if not forward_results:
        weakest = {"regime": "N/A", "loss_rate_pct": 0.0, "worlds": 0, "worst_drawdown_pct": 0.0}
        return {
            "strategy": {"type": strategy_type, "parameters": params},
            "baseline": historical,
            "forward_test": {
                "total_worlds": 0,
                "overall_loss_rate_pct": 0.0,
                "regimes": [],
                "stats": {
                    "strongest_regime": "N/A",
                    "high_loss_regimes": 0,
                    "acceptable_regimes": 0,
                    "median_regime_loss_pct": 0.0,
                    "median_worst_drawdown_pct": 0.0,
                },
            },
            "failure_summary": "No completed forward-test worlds were produced, so no regime-level failure pattern could be assessed.",
            "failure_analysis": {
                "regime_breakdowns": [],
                "pattern": "insufficient_data",
                "vulnerability_type": "insufficient_data",
                "historical_context": {
                    "return_robustness": "Unknown",
                    "regime_coverage": "Insufficient",
                    "risk_profile": "Unknown",
                    "base_drawdown": f"{abs(float(historical.get('max_drawdown_pct', 0.0))):.2f}%",
                },
                "segmentation_insights": {},
            },
            "correction_suggestion": "Increase worlds_per_regime, extend simulation duration, or widen execution/adjustment parameters so completed worlds are produced.",
            "limitations": "Synthetic regimes are diagnostic models, not forecasts. Results depend on the uploaded data and include a 2 bps turnover cost assumption.",
        }
    weakest = max(forward_results, key=lambda r: _f(r["loss_rate_pct"]))
    overall_worlds = sum(_i(r["worlds"]) for r in forward_results)
    total_losses = sum(int(_f(r["loss_rate_pct"]) / 100.0 * _i(r["worlds"])) for r in forward_results)
    overall_loss_rate = round(total_losses / overall_worlds * 100, 1) if overall_worlds else 0.0

    strongest = min(forward_results, key=lambda r: _f(r["loss_rate_pct"]))
    high_loss_regimes = [r for r in forward_results if _f(r["loss_rate_pct"]) > 50.0]
    median_drawdown = (
        round(_median([_f(r["worst_drawdown_pct"]) for r in forward_results]), 2)
        if len(forward_results) > 1
        else 0.0
    )

    tca_keys = (
        "slippage_vs_vwap",
        "slippage_vs_arrival",
        "opportunity_cost",
        "completion_rate_penalty_bps",
    )
    tca_summary: dict[str, float] = {}
    for key in tca_keys:
        values = [float(r[key]) for r in forward_results if r.get(key) is not None]
        if values:
            tca_summary[key] = round(_median(values), 4)

    forward_payload: dict[str, object] = {
        "total_worlds": overall_worlds,
        "overall_loss_rate_pct": overall_loss_rate,
        "regimes": forward_results,
        "stats": {
            "strongest_regime": strongest["regime"],
            "high_loss_regimes": len(high_loss_regimes),
            "acceptable_regimes": len(forward_results) - len(high_loss_regimes),
            "median_regime_loss_pct": round(_median([_f(r["loss_rate_pct"]) for r in forward_results]), 1),
            "median_worst_drawdown_pct": median_drawdown,
        },
    }
    if tca_summary:
        forward_payload["tca"] = tca_summary

    return {
        "strategy": {
            "type": strategy_type,
            "parameters": params,
        },
        "baseline": historical,
        "forward_test": forward_payload,
        "failure_summary": (
            f"Most vulnerable in {weakest['regime']} markets: "
            f"lost money in {weakest['loss_rate_pct']}% of {weakest['worlds']} unseen worlds, "
            f"worst drawdown {weakest['worst_drawdown_pct']}%."
        ),
        "failure_analysis": _build_failure_analysis(
            strategy_type, params, historical, forward_results, weakest, high_loss_regimes, median_drawdown
        ),
        "correction_suggestion": _suggest_correction(strategy_type, params, forward_results),
        "limitations": (
            "Synthetic regimes are diagnostic models, not forecasts. "
            "Results depend on the uploaded data and include a 2 bps turnover cost assumption."
        ),
    }


def _build_failure_analysis(
    strategy_type: str,
    params: dict[str, int],
    historical: dict[str, float | int],
    forward_results: list[dict[str, object]],
    weakest: dict[str, object],
    high_loss_regimes: list[dict[str, object]],
    median_drawdown: float,
) -> dict[str, object]:
    regime_breakdowns = [
        {
            "regime": r["regime"],
            "loss_rate_pct": r["loss_rate_pct"],
            "avg_return_pct": r.get("avg_return_pct", r["median_return_pct"]),
            "worst_drawdown_pct": r["worst_drawdown_pct"],
            "best_return_pct": r["best_return_pct"],
        }
        for r in forward_results
    ]

    hist_return = _f(historical.get("total_return_pct", 0.0))
    hist_sharpe = _f(historical.get("sharpe", 0.0))
    hist_drawdown = _f(historical.get("max_drawdown_pct", 0.0))

    pattern = _classify_regime_pattern(forward_results)
    vulnerability_type = _identify_vulnerability(strategy_type, pattern, weakest, high_loss_regimes)

    return {
        "regime_breakdowns": regime_breakdowns,
        "pattern": pattern,
        "vulnerability_type": vulnerability_type,
        "historical_context": {
            "return_robustness": "Strong"
            if hist_return > 15 and hist_sharpe > 1.0
            else "Moderate"
            if hist_return > 5
            else "Weak",
            "regime_coverage": "Acceptable" if len(forward_results) >= 4 else "Insufficient",
            "risk_profile": "High turnover" if _f(historical.get("turnover", 0.0)) > 5 else "Moderate",
            "base_drawdown": f"{abs(hist_drawdown):.2f}%",
        },
        "segmentation_insights": {
            f"{r['regime']}: {r['loss_rate_pct']}% loss rate": {
                "interpretation": _interpret_segment(strategy_type, r),
                "action": _segment_action(strategy_type, r, str(weakest["regime"])),
            }
            for r in forward_results
        },
    }


def _classify_regime_pattern(forward_results: list[dict[str, object]]) -> str:
    volatile_regimes = [
        r
        for r in forward_results
        if r["regime"] in ("High Volatility", "Sudden Selloff", "Sideways & Choppy")
    ]
    if not volatile_regimes:
        return "Unknown"
    avg_loss = sum(_f(r["loss_rate_pct"]) for r in volatile_regimes) / len(volatile_regimes)
    if avg_loss > 75:
        return "Extreme regime sensitivity"
    if avg_loss > 50:
        return "High regime sensitivity"
    if avg_loss > 25:
        return "Moderate regime sensitivity"
    return "Low regime sensitivity"


def _identify_vulnerability(
    strategy_type: str,
    pattern: str,
    weakest: dict[str, object],
    high_loss_regimes: list[dict[str, object]],
) -> str:
    if strategy_type == "sma_crossover" and weakest["regime"] in ("Sideways & Choppy", "High Volatility"):
        return "Whipsaw losses from noise"
    if strategy_type == "sma_per_crossover" and weakest["regime"] in ("High Volatility", "Sudden Selloff"):
        return "Trend reversal losses"
    if strategy_type == "breakout" and weakest["regime"] in ("Sideways & Choppy",):
        return "False breakout signals"
    if strategy_type == "rsi_reversion" and weakest["regime"] in ("Sideways & Choppy", "High Volatility"):
        return "Overtrading from RSI extremes"
    if pattern == "Extreme regime sensitivity":
        return "High sensitivity to market conditions"
    return "General regime-specific losses"


def _interpret_segment(strategy_type: str, segment: dict[str, object]) -> str:
    regime = segment["regime"]
    loss_rate = _f(segment["loss_rate_pct"])

    if regime == "Steady Trend":
        return (
            "Excellent performance in trending markets, strategy captures directional moves well."
            if loss_rate < 30
            else "Poor performance even in trends - consider parameter tuning."
        )

    if regime == "Sideways & Chppy":
        if strategy_type == "sma_crossover":
            return "Whipsaw losses from repeated false crossover signals in choppy price action."
        if strategy_type == "breakout":
            return "Breakout fails to break out - price oscillates near previous highs/lows."
        if strategy_type == "rsi_mean_reversion":
            return "RSI oscillates in neutral zone causing indecision and missed reversals."
        return "Strategy loses in sideways markets due to lack of clear direction."

    if regime == "High Volatility":
        if strategy_type == "sma_crossover":
            return "Lagging signals amplified by volatility cause late entries and exits."
        if strategy_type == "rsi_mean_reversion":
            return "RSI stays at extremes for extended periods in volatile regimes."
        return "Volatility causes strategy to produce incorrect signals."

    if regime == "Sudden Selloff":
        if strategy_type in ("rsi_mean_reversion"):
            return "Buying against falling prices leads to large losses."
        return "Strategy fails to detect reversal signals in rapid selloffs."

    return f"Loss rate of {loss_rate}% indicates poor adaptation to {regime} conditions."


def _segment_action(strategy_type: str, segment: dict[str, object], weakest_regime: str) -> str:
    regime = segment["regime"]
    loss_rate = _f(segment["loss_rate_pct"])

    if loss_rate < 20:
        return "No action needed - strategy performs well here."

    if regime == "Sideways & Choppy" and strategy_type == "sma_crossover":
        return "Increase slow window to reduce noise sensitivity."
    if regime == "High Volatility" and strategy_type in ("sma_crossover", "breakout"):
        return "Add volatility filter or widen confirmation threshold."
    if regime == "Sudden Selloff" and strategy_type == "rsi_mean_reversion":
        return "Add trend confirmation filter to avoid buying into selloffs."
    if regime == "Sideways & Choppy" and strategy_type == "rsi_mean_reversion":
        return "Widen RSI thresholds or reduce position size."
    if regime == "Sideways & Chppy" and strategy_type == "breakout":
        return "Increase breakout threshold or shorten holding period."

    return f"Investigate {regime} losses with regime-specific indicators."


def _suggest_correction(
    strategy_type: str, params: dict[str, int], forward_results: list[dict[str, object]]
) -> dict[str, object]:
    if not forward_results:
        return {
            "rationale": "No completed forward-test worlds were produced, so no failure pattern could be assessed.",
            "alternatives": [
                {
                    "label": "Increase worlds_per_regime",
                    "parameter_changes": {
                        "worlds_per_regime": max(10, params.get("worlds_per_regime", 10) + 10)
                    },
                    "reason": "More worlds increase the chance of completed simulations across all regimes.",
                },
                {
                    "label": "Extend simulation duration",
                    "parameter_changes": {"minutes": max(30, params.get("minutes", 30) + 30)},
                    "reason": "Longer duration gives execution bridges more steps to produce trade sequences.",
                },
                {
                    "label": "Reduce slippage assumptions",
                    "parameter_changes": {"impact_mode": "fixed", "spread_bps": 1},
                    "reason": "Lower synthetic cost pressure allows more worlds to complete with positive return.",
                },
            ],
        }
    weakest = max(forward_results, key=lambda r: _f(r["loss_rate_pct"]))
    suggestions: dict[str, object] = {
        "rationale": (
            f"The strategy failed most in {weakest['regime']} conditions. "
            f"Loss rate {weakest['loss_rate_pct']}% exceeds acceptable threshold."
        ),
        "alternatives": [],
    }
    if strategy_type == "sma_crossover":
        fast = params.get("fast", 20)
        slow = params.get("slow", 50)
        suggestions["alternatives"] = [
            {
                "label": "Slow down confirmation window",
                "parameter_changes": {"slow": max(slow + 10, round(slow * 1.5))},
                "reason": "A longer slow SMA reduces whipsaw entries in noisy sideways markets.",
            },
            {
                "label": "Add volatility filter",
                "parameter_changes": {"fast": max(fast, round(slow * 0.4))},
                "reason": "Require stronger signal by aligning fast window closer to slow trend.",
            },
            {
                "label": "Tighten fast window",
                "parameter_changes": {"fast": max(5, round(fast * 0.8))},
                "reason": "Faster response to trend changes in volatile regimes.",
            },
        ]
    elif strategy_type == "breakout":
        entry_lookback = params.get("entry_lookback", 20)
        exit_lookback = params.get("exit_lookback", 10)
        suggestions["alternatives"] = [
            {
                "label": "Widen entry threshold",
                "parameter_changes": {"entry_lookback": round(entry_lookback * 1.5)},
                "reason": "A higher entry barrier filters false breakouts in choppy conditions.",
            },
            {
                "label": "Tighten exit stop",
                "parameter_changes": {"exit_lookback": max(2, round(exit_lookback * 0.7))},
                "reason": "A faster exit limits drawdown during sudden reversals.",
            },
            {
                "label": "Add pullback confirmation",
                "parameter_changes": {"exit_lookback": max(3, round(exit_lookback * 1.2))},
                "reason": "Require confirmation before exiting to avoid premature exits.",
            },
        ]
    elif strategy_type == "rsi_reversion":
        oversold = params.get("oversold", 30)
        overbought = params.get("overbought", 70)
        suggestions["alternatives"] = [
            {
                "label": "Widen neutral zone",
                "parameter_changes": {
                    "oversold": max(5, oversold - 5),
                    "overbought": min(95, overbought + 5),
                },
                "reason": "A wider neutral band prevents overtrading in high-volatility regimes.",
            },
            {
                "label": "Narrow overbought threshold",
                "parameter_changes": {
                    "overbought": max(55, overbought - 5),
                },
                "reason": "Take profits earlier in trending markets.",
            },
            {
                "label": "Strengthen oversold signal",
                "parameter_changes": {
                    "oversold": max(5, oversold - 5),
                    "overbought": min(95, overbought + 3),
                },
                "reason": "Require stronger divergence before entering in choppy conditions.",
            },
        ]
    return suggestions


def format_report_text(report: dict[str, object]) -> str:
    return json.dumps(report, indent=2)
