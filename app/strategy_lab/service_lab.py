import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from app.strategy_lab.dsl import Strategy

_CANONICAL_EXCLUDES = {"strategy_id", "approval", "provenance", "conflict_report"}


def _canonical_strategy(strategy: Strategy) -> str:
    data = strategy.model_dump(mode="json", exclude_none=True)
    for key in list(_CANONICAL_EXCLUDES):
        data.pop(key, None)
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


class ApprovalService:
    @staticmethod
    def lock(spec: dict[str, Any], actor: str = "user") -> dict[str, Any]:
        spec["is_locked"] = True
        strategy = Strategy.model_validate(spec)
        canonical = _canonical_strategy(strategy)
        now = datetime.now(UTC).isoformat()
        approval = {
            "status": "approved",
            "approved_at": now,
            "approved_by": actor,
            "canonical_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "strategy_id": strategy.ledger_hash,
        }
        return approval
