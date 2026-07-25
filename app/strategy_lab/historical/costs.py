from __future__ import annotations

from typing import Any


class HistoricalCostModel:
    @staticmethod
    def estimate(spec: dict[str, Any], fills: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "commission_bps": spec.get("execution", {}).get("commission_bps", 0.0),
            "estimated_cost": 0.0,
        }
