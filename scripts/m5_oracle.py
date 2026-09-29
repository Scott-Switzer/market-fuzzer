#!/usr/bin/env python3
"""M5.1 differential oracle: FWF kernel vs. an independent accounting engine.

Runs 100 deterministic, *valid* business-event sequences through both
implementations and compares the normalized, cent-exact trial balance after
**every** event. This milestone is the one place Market Fuzzer asserts that the
vendored kernel is not merely self-consistent but arithmetically correct against
an implementation it did not write.

Two independent claims are established, and both are required for PASS:

1. **Agreement** — all 100 sequences produce zero non-zero trial-balance deltas
   against ``python-accounting==1.0.1``, and every event type is exercised.
2. **Sensitivity** — agreement is only meaningful if the harness can actually
   *see* a wrong number. So six realistic single-posting defects are planted in
   the FWF side (reversed debit/credit, omitted postings, a misclassification,
   a skewed equity split, a broken period close) and each must be detected by
   the **unmodified** oracle, localized to the event that caused it.

If the oracle cannot detect a deliberately broken FWF side, it cannot certify a
correct one, so claim 2 gates claim 1 rather than decorating it.

A failing sequence is never discarded or regenerated: its seed is reported so it
replays directly.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from collections.abc import Sequence as SequenceABC
from datetime import UTC, datetime
from decimal import Decimal
from importlib import metadata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.m5_vendor import VendorDrift, verify_all  # noqa: E402
from tests.m5.chart import FWF_ACCOUNTS  # noqa: E402
from tests.m5.events import EventType  # noqa: E402
from tests.m5.planted import (  # noqa: E402
    DEFECTS,
    find_target_sequence,
    run_planted,
)
from tests.m5.runner import run_sequences, unsupported_operations  # noqa: E402
from tests.m5.sequences import BASE_SEED, generate_sequences  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO / "artifacts" / "m5"

ZERO = Decimal("0.00")

#: The gate's fixed shape. 100 sequences, the full event-type inventory, and
#: every planted defect detected.
SEQUENCE_COUNT = 100


def _library_versions() -> dict[str, str]:
    """Report the versions of both sides of the comparison.

    The oracle's version comes from its installed distribution metadata; the
    kernel is a vendored source snapshot with no distribution, so it is
    imported through the same ``app/_vendor`` path the test bootstrap uses and
    its ``__version__`` attribute is read directly. Both are load-bearing: the
    evidence artifact is worthless unless it names the exact oracle build the
    100-sequence agreement was measured against.
    """
    versions: dict[str, str] = {}
    try:
        versions["python_accounting"] = metadata.version("python-accounting")
    except metadata.PackageNotFoundError:  # pragma: no cover - env is pinned
        versions["python_accounting"] = "NOT INSTALLED"

    vendor_root = str(REPO / "app" / "_vendor")
    if vendor_root not in sys.path:
        sys.path.insert(0, vendor_root)
    try:
        import fwf_kernel

        versions["fwf_kernel"] = getattr(fwf_kernel, "__version__", "unknown")
    except Exception as exc:  # pragma: no cover - reported, not raised
        versions["fwf_kernel"] = f"IMPORT FAILED: {exc}"

    try:
        versions["hypothesis"] = metadata.version("hypothesis")
    except metadata.PackageNotFoundError:  # pragma: no cover
        versions["hypothesis"] = "not installed"
    return versions


def run_agreement(sequences) -> dict[str, Any]:
    """Run every sequence and summarize agreement, coverage and divergence."""
    started = time.time()
    results, coverage = run_sequences(sequences)
    elapsed = time.time() - started

    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]
    checkpoints = sum(r.checkpoints for r in results)
    matched = sum(r.matched_checkpoints for r in results)
    events = sum(r.events for r in results)

    uncovered = sorted(e.value for e in EventType if coverage.get(e.value, 0) == 0)

    return {
        "sequences": len(results),
        "passed": len(passed),
        "failed": len(failed),
        "events_applied": events,
        "checkpoints_compared": checkpoints,
        "checkpoints_matched": matched,
        "trial_balance_deltas_all_zero": matched == checkpoints,
        "accounts_compared": len(FWF_ACCOUNTS),
        "event_type_coverage": dict(sorted(coverage.items())),
        "event_types_total": len(EventType),
        "event_types_uncovered": uncovered,
        "mismatches": [r.mismatch.as_dict() for r in failed if r.mismatch],
        "elapsed_seconds": round(elapsed, 1),
        "result": "PASS" if not failed and not uncovered and matched == checkpoints else "FAIL",
    }


def run_sensitivity(sequences) -> dict[str, Any]:
    """Plant every defect and require the unmodified oracle to catch each one."""
    results = []
    for defect in DEFECTS:
        try:
            target = find_target_sequence(defect, sequences)
        except LookupError as exc:  # pragma: no cover - generator regression
            results.append(
                {
                    **defect.as_dict(),
                    "injection_applied": False,
                    "detected_by_oracle": False,
                    "sequence_seed": None,
                    "detail": str(exc),
                }
            )
            continue
        outcome = run_planted(defect, target)
        results.append({**defect.as_dict(), **outcome.as_dict()})

    detected_ids = {
        r["defect_id"]
        for r in results
        if r["injection_applied"] and r["detected_by_oracle"] and r["event_index"] is not None
    }
    # A defect counts as caught only if it was actually injected, the oracle
    # flagged it, and the divergence was localized to a concrete event index.
    missed = [r["defect_id"] for r in results if r["defect_id"] not in detected_ids]

    return {
        "defects": len(DEFECTS),
        "detected": len(detected_ids),
        "missed": missed,
        "all_detected": not missed,
        "results": results,
        "result": "PASS" if not missed else "FAIL",
    }


def build_report(agreement: dict[str, Any], sensitivity: dict[str, Any], vendor) -> dict[str, Any]:
    ok = agreement["result"] == "PASS" and sensitivity["result"] == "PASS"
    return {
        "schema_version": 1,
        "gate": "M5.1",
        "title": "Independent accounting oracle (python-accounting differential)",
        "timestamp": datetime.now(UTC).isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "library_versions": _library_versions(),
        "base_seed": BASE_SEED,
        "sequence_count": SEQUENCE_COUNT,
        "deterministic": True,
        "vendor_integrity": {
            "source_commit": vendor["source_commit"],
            "files_verified": vendor["files_verified"],
            "all_match": vendor["all_match"],
            "groups": [g["manifest"] for g in vendor["groups"]],
        },
        "agreement": agreement,
        "sensitivity": sensitivity,
        "unsupported_operations": unsupported_operations(),
        "result": "PASS" if ok else "FAIL",
    }


def main(argv: SequenceABC[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M5.1 differential accounting oracle")
    parser.add_argument(
        "--count",
        type=int,
        default=SEQUENCE_COUNT,
        help="Number of deterministic sequences (the M5 gate requires 100).",
    )
    args = parser.parse_args(argv)

    try:
        vendor = verify_all()
    except VendorDrift as exc:
        print(f"VENDOR DRIFT: {exc}", file=sys.stderr)
        return 2

    sequences = generate_sequences(count=args.count, base_seed=BASE_SEED)
    agreement = run_agreement(sequences)
    sensitivity = run_sensitivity(sequences)
    report = build_report(agreement, sensitivity, vendor)
    report["sequence_count"] = args.count

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    out = ARTIFACTS / "oracle_report.json"
    out.write_text(json.dumps(report, indent=2) + "\n")

    print(
        f"M5.1 {report['result']}: "
        f"sequences={agreement['sequences']} passed={agreement['passed']} "
        f"deltas_all_zero={agreement['trial_balance_deltas_all_zero']} "
        f"coverage={agreement['event_types_total'] - len(agreement['event_types_uncovered'])}"
        f"/{agreement['event_types_total']} "
        f"planted_detected={sensitivity['detected']}/{sensitivity['defects']} "
        f"-> {out}"
    )
    if agreement["mismatches"]:
        for mismatch in agreement["mismatches"][:3]:
            print(
                f"  MISMATCH seed={mismatch['seed']} event={mismatch['event_index']} "
                f"({mismatch['event_type']}) deltas={mismatch['per_account_deltas']}",
                file=sys.stderr,
            )
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
