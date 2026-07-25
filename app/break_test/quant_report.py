"""Strategy ranking report helpers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def math_sqrt(x: float) -> float:
    import math

    return math.sqrt(max(x, 0.0))


def rank_strategies(results: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lexicographic multi-criteria ranking of strategy result dicts.

    Criteria (higher better unless noted):
      1. regime_efficacy (or sharpe)
      2. -tail_sensitivity (lower drawdown magnitude better)
      3. turnover_normalized_sharpe
    """
    ranked: list[dict[str, Any]] = []
    for idx, row in enumerate(results):
        sharpe = float(row.get("sharpe", row.get("oos_sharpe", 0.0)) or 0.0)
        regime_eff = float(row.get("regime_efficacy", sharpe) or 0.0)
        max_dd = abs(float(row.get("max_drawdown_pct", row.get("tail_sensitivity", 0.0)) or 0.0))
        turnover = max(float(row.get("turnover", 1.0)) or 1.0, 1e-6)
        tn_sharpe = sharpe / math_sqrt(turnover)
        ranked.append(
            {
                **dict(row),
                "_index": idx,
                "regime_efficacy": regime_eff,
                "tail_sensitivity": max_dd,
                "turnover_normalized_sharpe": round(tn_sharpe, 6),
                "rank_key": (regime_eff, -max_dd, tn_sharpe),
            }
        )
    ranked.sort(key=lambda r: r["rank_key"], reverse=True)
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank
        row.pop("rank_key", None)
    return ranked
