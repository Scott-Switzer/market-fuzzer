from __future__ import annotations

import hashlib
import json

from app.strategy_lab.dsl import Strategy


def canonicalize_strategy_spec(spec: Strategy) -> str:
    data = spec.model_dump(mode="json", exclude_none=True)
    for key in ["strategy_id", "approval", "provenance", "conflict_report"]:
        data.pop(key, None)
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def strategy_spec_hash(spec: Strategy) -> str:
    return hashlib.sha256(canonicalize_strategy_spec(spec).encode("utf-8")).hexdigest()
