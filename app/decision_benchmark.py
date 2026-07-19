"""Decision-level evidence for the Synthetic Market World demo."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.execution_arena import benchmark_matrix


def build_decision_change_benchmark(seeds: tuple[int, ...] = (41, 42)) -> dict[str, Any]:
    """Return a deterministic, customer-readable benchmark decision record.

    The record deliberately compares the visible public objective with the
    protected robustness objective. It is evidence of a changed research
    decision inside the declared synthetic worlds, not a profitability claim.
    """
    matrix = benchmark_matrix(seeds=seeds)
    rows = matrix["rows"]
    public_winner = min(rows, key=lambda row: (row["public_rank"], row["policy_id"]))
    robust_winner = min(rows, key=lambda row: (row["robustness_rank"], row["policy_id"]))
    decision_changed = public_winner["policy_id"] != robust_winner["policy_id"]
    record = {
        "benchmark_id": "decision-change-v1",
        "seeds": list(seeds),
        "public_winner": {
            "policy_id": public_winner["policy_id"],
            "rank": public_winner["public_rank"],
        },
        "protected_robustness_winner": {
            "policy_id": robust_winner["policy_id"],
            "rank": robust_winner["robustness_rank"],
        },
        "decision_changed": decision_changed,
        "decision": (
            f"Prefer {robust_winner['policy_id']} for the protected stress case; "
            f"{public_winner['policy_id']} only wins the visible objective."
            if decision_changed
            else "No policy switch observed for this seed contract."
        ),
        "evidence": {
            "matrix_hash": matrix["provenance"]["matrix_hash"],
            "public_objective": "public_score",
            "protected_objective": "robustness_score",
            "claim_boundary": "Synthetic benchmark decision evidence; not a profitability or live-execution claim.",
        },
    }
    record["artifact_hash"] = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return record
