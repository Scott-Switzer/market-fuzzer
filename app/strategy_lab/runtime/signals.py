from __future__ import annotations

from typing import Any


class StrategySignalEngine:
    @staticmethod
    def validate_signal(signal: dict[str, Any]) -> dict[str, Any]:
        required = {"id", "type"}
        missing = required - signal.keys()
        if missing:
            raise ValueError(f"missing signal fields: {sorted(missing)}")
        return signal
