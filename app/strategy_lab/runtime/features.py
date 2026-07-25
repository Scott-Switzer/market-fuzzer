from __future__ import annotations

from typing import Any


class StrategyFeatureEngine:
    @staticmethod
    def validate_feature(feature: dict[str, Any]) -> dict[str, Any]:
        required = {"id", "source", "field", "lag_policy"}
        missing = required - feature.keys()
        if missing:
            raise ValueError(f"missing feature fields: {sorted(missing)}")
        return feature
