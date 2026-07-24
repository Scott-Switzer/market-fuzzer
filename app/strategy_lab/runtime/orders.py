from __future__ import annotations

from typing import Any


class StrategyOrderEngine:
    @staticmethod
    def validate_order(order: dict[str, Any]) -> dict[str, Any]:
        required = {"order_type", "decision_time", "fill_time"}
        missing = required - order.keys()
        if missing:
            raise ValueError(f"missing order fields: {sorted(missing)}")
        return order
