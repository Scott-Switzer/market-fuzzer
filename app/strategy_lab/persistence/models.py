from __future__ import annotations

from typing import Any


class PersistenceModels:
    @staticmethod
    def strategy_version_model() -> dict[str, Any]:
        return {
            "strategy_id": "string",
            "version_id": "string",
            "canonical_hash": "string",
            "is_locked": False,
            "frozen_at": None,
        }
