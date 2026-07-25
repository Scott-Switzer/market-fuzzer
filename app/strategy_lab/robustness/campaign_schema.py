from __future__ import annotations

from typing import Any


class CampaignSchema:
    @staticmethod
    def default_payload() -> dict[str, Any]:
        return {
            "stages": [
                "create",
                "validate_lock",
                "select_worlds",
                "baseline",
                "broad_search",
                "targeted_search",
                "confirm",
                "minimize",
                "replay",
                "attribution",
                "suggest",
                "report",
            ]
        }
