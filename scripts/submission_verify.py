"""verify-submission gate: runs the full pipeline on the offline fixture and
checks the honesty invariants (hash invariant, no fabricated evidence, real trades).
Exits non-zero on any failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.strategy_lab.submission.evidence import build_evidence_package
from app.strategy_lab.submission.orchestrator import run_submission
from app.strategy_lab.submission.strategy import CrossSectionalSpec


def main() -> int:
    spec = CrossSectionalSpec()
    run = run_submission(spec=spec, mode="synthetic_fixture", budget=24)
    errors: list[str] = []

    # 1) strategy hash invariant across stages
    if run.approval["strategy_id"] != run.strategy_hash:
        errors.append("approval id != strategy hash")
    if run.stress["strategy_hash"] != run.strategy_hash:
        errors.append("stress strategy_hash != strategy hash")

    # 2) backtest produced real activity (not fabricated flat)
    if run.backtest["metrics"]["sharpe"] == 0.0 or len(run.backtest["trades"]) == 0:
        errors.append("historical backtest has zero trades / zero sharpe (fabricated?)")

    # 3) evidence artifact hashes are non-trivial
    ev = build_evidence_package(run)
    m = ev["manifest"]
    if not m["artifact_hashes"].get("equity_curve.csv"):
        errors.append("equity_curve.csv artifact hash missing")

    # 4) deck_data exists and carries the same hash
    deck = json.loads((Path(ev["base_dir"]) / "pitch" / "deck_data.json").read_text())
    if deck["strategy_hash"] != run.strategy_hash:
        errors.append("deck_data strategy_hash mismatch")

    if errors:
        print("SUBMISSION VERIFY: FAIL")
        for e in errors:
            print("  -", e)
        return 1
    print("SUBMISSION VERIFY: PASS")
    print(
        f"  strategy_hash={run.strategy_hash[:16]} failures={run.stress['failure_count']}/{run.stress['evaluated']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
