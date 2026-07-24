"""verify-submission gate: runs the full pipeline on the offline fixture and
checks the honesty invariants (hash invariant, no fabricated evidence, real
trades, and the 5.1 claims-manifest guardrail).

Exits non-zero on any failure.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.strategy_lab.submission.evidence import build_evidence_package
from app.strategy_lab.submission.orchestrator import run_submission
from app.strategy_lab.submission.strategy import CrossSectionalSpec

# 5.1 Required protected fields — every one MUST remain False.
REQUIRED_FALSE = [
    "live_profitability",
    "production_execution_fidelity",
    "universal_realism",
    "exhaustive_failure_discovery",
    "validated_cost_calibration",
    "arbitrary_strategy_support",
    "institutional_data_rights",
]


def _git_sha() -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=Path.cwd())
    return out.stdout.strip()


def main() -> int:
    spec = CrossSectionalSpec()
    run = run_submission(spec=spec, mode="synthetic_fixture", budget=24)
    errors: list[str] = []
    sha = _git_sha()

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
    if not m["artifact_hashes"].get("historical/equity_curve.csv"):
        errors.append("historical/equity_curve.csv artifact hash missing")

    # 4) deck_data exists and carries the same hash + watermark
    deck = json.loads((Path(ev["base_dir"]) / "pitch" / "deck_data.json").read_text())
    if deck["strategy_hash"] != run.strategy_hash:
        errors.append("deck_data strategy_hash mismatch")
    if "tier_watermark" not in deck:
        errors.append("deck_data missing per-tier watermark")

    # 5.1) claims manifest present, complete, and all protected values False
    claims_path = Path(ev["base_dir"]) / "pitch" / "CLAIMS_MANIFEST.json"
    if not claims_path.exists():
        errors.append("CLAIMS_MANIFEST.json missing")
    else:
        claims = json.loads(claims_path.read_text())
        for field in REQUIRED_FALSE:
            if field not in claims:
                errors.append(f"claims manifest missing required field: {field}")
            elif claims[field] is not False:
                errors.append(f"claims manifest protected value became true: {field}")
        if claims.get("cost_model_calibrated") is not False:
            errors.append("cost_model_calibrated must be False")

    # 5.2) claims digest bound into submission manifest
    if m.get("claims_manifest_hash") != ev_manifest_claims_digest(claims_path):
        errors.append("submission manifest claims digest mismatch")

    # 6) deck must not claim historical performance when in fixture mode
    if deck["data_mode"] == "synthetic_fixture":
        if "HISTORICAL" in (deck.get("historical", {}).get("note", "") or "").upper():
            errors.append("fixture deck must not label results historical")

    if errors:
        print("SUBMISSION VERIFY: FAIL")
        for e in errors:
            print("  -", e)
        return 1
    print("SUBMISSION VERIFY: PASS")
    print(
        f"  strategy_hash={run.strategy_hash[:16]} "
        f"failures={run.stress['failure_count']}/{run.stress['evaluated']} "
        f"git_sha={sha[:12]}"
    )
    return 0


def ev_manifest_claims_digest(claims_path: Path) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(json.loads(claims_path.read_text()), sort_keys=True).encode()
    ).hexdigest()


if __name__ == "__main__":
    sys.exit(main())
