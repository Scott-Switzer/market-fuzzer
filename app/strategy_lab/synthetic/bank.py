from __future__ import annotations

from typing import Any


class SyntheticWorldBank:
    @staticmethod
    def list_families() -> list[dict[str, Any]]:
        return [
            {"family_id": "regime_switching_factor", "version": "0.1.0"},
            {"family_id": "block_bootstrap_residual", "version": "0.1.0"},
        ]
