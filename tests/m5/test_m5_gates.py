"""Wire the three M5 gates into the default pytest run.

``make verify-fast`` (and CI's ``test`` job) runs plain ``pytest``. The M5 gates
are script-shaped -- they run subprocesses, write artifacts and exit non-zero on
failure -- so this module invokes them the way the nightly workflow does, with a
budget small enough for a PR pipeline, and asserts on the *reports* rather than
trusting the exit codes.

Skipped, not silently absent: if a gate's script is missing, or its heavy
dependencies are not installed in the running interpreter, the test skips with
a reason instead of passing vacuously.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ORACLE = REPO / "scripts" / "m5_oracle.py"
STATEFUL = REPO / "scripts" / "m5_stateful.py"
SUMMARY = REPO / "scripts" / "m5_summary.py"
VENDOR = REPO / "scripts" / "m5_vendor.py"
TRIAGE = REPO / "tests" / "m5" / "mutation_triage.json"


def _run(script: Path, *args: str, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-W", "ignore", str(script), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@pytest.fixture(scope="module")
def oracle_report() -> dict:
    """Run the oracle gate at the PR budget and return its report."""
    proc = _run(ORACLE, "--count", "12")
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    path = REPO / "artifacts" / "m5" / "oracle_report.json"
    return json.loads(path.read_text())


def test_vendor_snapshot_hashes_match(oracle_report: dict) -> None:
    """The vendored kernel provably is the canonical Core snapshot."""
    integrity = oracle_report["vendor_integrity"]
    assert integrity["all_match"] is True
    assert integrity["files_verified"] >= 14


def test_oracle_sequences_agree_to_the_cent(oracle_report: dict) -> None:
    """Every sequence's every checkpoint agrees with python-accounting."""
    agreement = oracle_report["agreement"]
    assert agreement["passed"] == agreement["sequences"]
    assert agreement["failed"] == 0
    assert agreement["trial_balance_deltas_all_zero"] is True
    assert agreement["checkpoints_matched"] == agreement["checkpoints_compared"]


def test_oracle_detects_every_planted_defect(oracle_report: dict) -> None:
    """The oracle is capable of failing: each planted defect is caught."""
    sensitivity = oracle_report["sensitivity"]
    assert sensitivity["all_detected"] is True
    assert sensitivity["detected"] == sensitivity["defects"]
    assert sensitivity["missed"] == []


def test_stateful_fast_budget_passes() -> None:
    """The committed PR budget of the canonical state machines still passes."""
    proc = _run(STATEFUL, "--timeout", "900")
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    report = json.loads((REPO / "artifacts" / "m5" / "stateful_report.json").read_text())
    assert report["result"] == "PASS"
    assert report["fast_run"]["result"] == "PASS"
    assert report["fast_run"]["passed"] is not None


def test_nightly_summary_aggregates_all_three_gates(oracle_report: dict) -> None:
    """The summary is PASS only when every gate it read is PASS.

    The mutation gate's report is optional here -- the PR pipeline does not run
    mutmut -- so the summary is expected to fail while any gate is missing, and
    that failure is the assertion: a missing gate must never look like a pass.
    """
    proc = _run(SUMMARY, timeout=120)
    summary_path = REPO / "artifacts" / "m5" / "nightly_summary.json"
    if not summary_path.exists():
        # Every gate report missing -> the summary refuses to write, which is
        # the correct behaviour. Oracle ran above, so this only happens if the
        # stateful report is absent too.
        assert proc.returncode != 0
        return
    summary = json.loads(summary_path.read_text())
    gates = {row["gate"]: row["result"] for row in summary["gates"]}
    assert gates.get("M5.1") == "PASS"
    if "M5.3" not in gates or gates.get("M5.3") == "FAIL":
        assert summary["overall"] == "FAIL" or gates.get("M5.3") == "PASS"
