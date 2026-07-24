"""Validation quality scoring combining hardenend evidence a quant would trust.

Aggregates multiple gates into a single scalar score and pass/fail verdict.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def _default_thresholds() -> dict[str, float]:
    return {
        "deflated_sharpe_min": 0.5,
        "pbo_max": 0.05,
        "turnover_per_year_max": 2.0,
        "max_drawdown_pct_max": -15.0,
        "regime_robustness_min": 0.4,
    }


def _norm_score(value: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0 if value < lo else 1.0
    if value <= lo:
        return 0.0
    if value >= hi:
        return 1.0
    return (value - lo) / (hi - lo)


def _penalty_score(value: float, threshold: float, scale: float = 1.0) -> float:
    """Higher is better; transitions to 0 at threshold, negative beyond."""
    diff = value - threshold
    if diff >= 0.0:
        return 1.0
    return max(0.0, 1.0 + diff / max(abs(threshold * scale), 1e-9))


def _regime_robustness_score(forward_regimes: list[dict[str, Any]] | None) -> float:
    if not forward_regimes:
        return 0.0
    median_returns = [float(r.get("median_return_pct", 0.0) or 0.0) for r in forward_regimes]
    worst_dd = [float(r.get("worst_drawdown_pct", 0.0) or 0.0) for r in forward_regimes]
    loss_rate = [float(r.get("loss_rate_pct", 100.0) or 100.0) for r in forward_regimes]
    avg_return = float(np.mean(median_returns)) if median_returns else 0.0
    avg_dd = float(np.mean([abs(x) for x in worst_dd])) if worst_dd else 1.0
    avg_loss = float(np.mean(loss_rate)) if loss_rate else 100.0
    efficacy = avg_return - 0.25 * avg_loss
    tail = avg_dd
    score = _norm_score(efficacy, 0.0, 15.0) + _norm_score(20.0 - tail, 0.0, 20.0)
    return float(np.clip(score / 2.0, 0.0, 1.0))


def validation_quality_score(
    *,
    deflated_sharpe: float,
    pbo: float,
    turnover: float,
    max_drawdown_pct: float,
    regime_robustness: float | None = None,
    forward_regimes: list[dict[str, Any]] | None = None,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Return a quant-grade validation quality score.

    Parameters
    ----------
    deflated_sharpe:
        Deflated Sharpe ratio after multiple testing correction.
    pbo:
        Probability of backtest overfitting, in ``[0, 1]``.
    turnover:
        Annualized turnover units (e.g. ~sum of abs position changes per year).
    max_drawdown_pct:
        Maximum drawdown expressed as a negative percentage (e.g. ``-12.3``).
    regime_robustness / forward_regimes:
        Either an explicit scalar in ``[0, 1]`` or a list of regime forward-test rows
        from which robustness is computed.
    thresholds:
        Optional overrides for hard gates.

    Returns
    -------
    dict[str, Any]
        ``score``, ``components``, ``pass``, and ``reasons``.
    """
    thresh = {**_default_thresholds(), **(thresholds or {})}
    robustness = (
        float(regime_robustness)
        if regime_robustness is not None
        else _regime_robustness_score(forward_regimes)
    )

    components = {
        "deflated_sharpe": round(float(deflated_sharpe), 4),
        "pbo": round(float(pbo), 4),
        "turnover_per_year": round(float(turnover), 4),
        "max_drawdown_pct": round(float(max_drawdown_pct), 4),
        "regime_robustness": round(float(robustness), 4),
    }

    rules = [
        ("deflated_sharpe", float(deflated_sharpe), float(thresh["deflated_sharpe_min"])),
        ("pbo", float(thresh["pbo_max"]) - float(pbo), 0.0),
        ("turnover_per_year", float(thresh["turnover_per_year_max"]) - float(turnover), 0.0),
        ("max_drawdown_pct", float(max_drawdown_pct), float(thresh["max_drawdown_pct_max"])),
        ("regime_robustness", float(robustness), float(thresh["regime_robustness_min"])),
    ]

    ok = []
    failed = []
    for name, value, limit in rules:
        if name == "max_drawdown_pct":
            passed = float(value) >= float(limit)
        else:
            passed = float(value) >= float(limit)
        (ok if passed else failed).append(name)

    gate_frac = len(ok) / max(len(rules), 1)
    score = round(float(gate_frac * 100), 2)
    passed = len(failed) == 0
    reasons = [f"passed:{name}" for name in ok] + [f"failed:{name}" for name in failed]

    return {
        "score": score,
        "components": components,
        "pass": passed,
        "gates_passed": len(ok),
        "gates_total": len(rules),
        "reasons": reasons,
        "thresholds_used": thresh,
    }


__all__ = ["validation_quality_score"]
