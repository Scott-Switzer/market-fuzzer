from __future__ import annotations

from typing import Any


class RegimeLibrary:
    @staticmethod
    def available() -> list[dict[str, Any]]:
        return [
            {"id": "steady_trend", "mean": 0.0003, "vol": 0.012},
            {"id": "sideways_choppy", "mean": 0.0, "vol": 0.018},
            {"id": "high_volatility", "mean": -0.0005, "vol": 0.035},
            {"id": "sudden_selloff", "mean": -0.025, "vol": 0.06},
        ]
