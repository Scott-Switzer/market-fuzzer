#!/usr/bin/env python3
"""Aggregate the three M5 gate reports into one nightly summary.

The nightly workflow runs three independent gates and each writes its own
machine-readable report:

* **M5.1** ``scripts/m5_oracle.py``      -> ``artifacts/m5/oracle_report.json``
* **M5.2** ``scripts/m5_stateful.py``    -> ``artifacts/m5/stateful_report.json``
* **M5.3** ``scripts/m5_mutation.py``    -> ``artifacts/m5/mutation_survivors.json``

``nightly_summary.json`` is the single document a reviewer reads first: one row
per gate, the headline number each gate is judged on, and an overall verdict
that is PASS only when every gate passed. A missing or unparseable report is a
FAIL for that gate -- a summary that silently skipped a gate would be worth less
than none.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO / "artifacts" / "m5"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{path.name} not found; run its gate first")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc


def oracle_row(report: dict[str, Any]) -> dict[str, Any]:
    agreement = report.get("agreement", {})
    sensitivity = report.get("sensitivity", {})
    total = agreement.get("sequences", 0)
    all_zero = agreement.get("trial_balance_deltas_all_zero", False)
    clean = sensitivity.get("all_detected", False)
    detected, planted = sensitivity.get("detected", 0), sensitivity.get("defects", 0)
    return {
        "gate": "M5.1",
        "title": "python-accounting differential oracle",
        "result": "PASS" if all_zero and total and clean else "FAIL",
        "headline": (
            f"{agreement.get('passed', 0)}/{total} sequences agree, "
            f"all trial-balance deltas 0.00, {detected}/{planted} planted defects detected"
        ),
        "report": "artifacts/m5/oracle_report.json",
    }


def stateful_row(report: dict[str, Any]) -> dict[str, Any]:
    nightly = report.get("nightly_run", {})
    return {
        "gate": "M5.2",
        "title": "Hypothesis nightly stateful profile",
        "result": report.get("result", "FAIL"),
        "headline": (
            f"{nightly.get('passed', '?')} machine tests passed at "
            f"{report.get('required_nightly_examples', 1000)} examples "
            f"in {nightly.get('elapsed_seconds', '?')}s"
        ),
        "report": "artifacts/m5/stateful_report.json",
    }


def mutation_row(report: dict[str, Any]) -> dict[str, Any]:
    untriaged = report.get("untriaged", 0)
    gaps = report.get("real_test_gap", 0)
    triage_path = REPO / "tests" / "m5" / "mutation_triage.json"
    triage: dict[str, Any] = {}
    if triage_path.exists():
        try:
            triage = json.loads(triage_path.read_text())
        except json.JSONDecodeError:
            gaps = gaps or 1
    buckets = triage.get("buckets", {})
    result = "PASS" if untriaged == 0 and buckets.get("REAL_TEST_GAP", gaps) == 0 else "FAIL"
    return {
        "gate": "M5.3",
        "title": "mutmut over ledger/subledgers/equity/filings",
        "result": result,
        "headline": (
            f"{report.get('killed', 0)}/{report.get('generated_mutants', 0)} killed, "
            f"{untriaged} untriaged, "
            f"{buckets.get('REAL_TEST_GAP', gaps)} REAL_TEST_GAP, "
            f"{buckets.get('EQUIVALENT_MUTANT', 0)} equivalent, "
            f"{buckets.get('OUT_OF_M5_SCOPE', 0)} diagnostic-only"
        ),
        "report": "artifacts/m5/mutation_survivors.json",
    }


def build_summary(
    oracle: dict[str, Any], stateful: dict[str, Any], mutation: dict[str, Any]
) -> dict[str, Any]:
    rows = [oracle_row(oracle), stateful_row(stateful), mutation_row(mutation)]
    overall = "PASS" if all(row["result"] == "PASS" for row in rows) else "FAIL"
    return {
        "schema_version": 1,
        "gate": "M5-nightly",
        "title": "Financial World Factory M5 nightly gate summary",
        "generated": datetime.now(UTC).isoformat(),
        "overall": overall,
        "gates": rows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate M5 gate reports")
    parser.add_argument(
        "--artifacts", type=Path, default=ARTIFACTS, help="Directory holding the gate reports"
    )
    args = parser.parse_args(argv)

    try:
        oracle = _load(args.artifacts / "oracle_report.json")
        stateful = _load(args.artifacts / "stateful_report.json")
        mutation = _load(args.artifacts / "mutation_survivors.json")
    except (FileNotFoundError, ValueError) as exc:
        print(f"NIGHTLY SUMMARY FAIL: {exc}", file=sys.stderr)
        return 2

    summary = build_summary(oracle, stateful, mutation)
    out = args.artifacts / "nightly_summary.json"
    out.write_text(json.dumps(summary, indent=2) + "\n")

    for row in summary["gates"]:
        print(f"  {row['gate']}  {row['result']:4s}  {row['headline']}")
    print(f"NIGHTLY SUMMARY {summary['overall']} -> {out}")
    return 0 if summary["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
