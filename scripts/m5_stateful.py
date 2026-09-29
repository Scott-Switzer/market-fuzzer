#!/usr/bin/env python3
"""M5.2 nightly stateful gate: the canonical FWF kernel machines at 1000 examples.

The Financial System Core kernel ships three Hypothesis state machines
(``LedgerMachine``, ``OperationalBookMachine``, ``EquityMachine``) whose PR-level
budgets are deliberately small. M5.2 raises them to 1000 examples on a nightly
schedule *without changing what they assert*, so a rare state-machine path that
PRs never reach still has to hold the kernel's invariants.

Two budgets are run and both must pass:

* **fast** — the committed PR budget (no ``FWF_NIGHTLY``). Run first, because a
  broken fast budget makes the nightly result uninterpretable.
* **nightly** — 1000 examples, identical machines, identical invariants.

The resolved budget is not taken on trust from the report: ``machine_settings``
is imported directly and every machine's effective ``max_examples`` and
``stateful_step_count`` is read back, so a silent budget regression fails the
gate instead of quietly reducing coverage.

The test files are the canonical bytes vendored under
``tests/m5/kernel_stateful`` and hash-pinned in its ``SOURCE.json``; the vendored
kernel snapshot is hash-pinned separately. Both are re-verified here, so this
gate provably exercises the shipped kernel rather than a paraphrase of it.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.m5_vendor import VendorDrift, verify_all  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO / "artifacts" / "m5"
SUITE = "tests/m5/kernel_stateful"

#: The M5 contract: the nightly budget is exactly this many examples per machine.
REQUIRED_NIGHTLY_EXAMPLES = 1000

#: The three canonical machines as ``(name, fast max_examples, stateful_step_count)``.
#: The step counts are asserted unchanged under the nightly profile: a raised
#: example budget with shrunken steps explores fewer transitions, not more.
MACHINES: tuple[tuple[str, int, int], ...] = (
    ("LedgerMachine", 60, 40),
    ("OperationalBookMachine", 50, 35),
    ("EquityMachine", 30, 25),
)


def _resolved_budgets(nightly: bool) -> dict[str, dict[str, int | None]]:
    """Read back the effective settings ``machine_settings`` resolves per machine.

    ``examples()`` consults ``FWF_NIGHTLY`` on every call rather than caching it,
    so the flag is simply set before this runs and each machine is asked for its
    own settings. Reading the resolved object back is what makes a silent budget
    regression fail the gate instead of quietly reducing coverage.
    """
    import os

    if nightly:
        os.environ["FWF_NIGHTLY"] = "1"
    else:
        os.environ.pop("FWF_NIGHTLY", None)

    from tests.m5.kernel_stateful import machine_settings as ms

    resolved: dict[str, dict[str, int | None]] = {}
    for name, fast_examples, steps in MACHINES:
        settings_obj = ms.machine_settings(max_examples=fast_examples, stateful_step_count=steps)
        resolved[name] = {
            "fast_max_examples": fast_examples,
            "max_examples": settings_obj.max_examples,
            "stateful_step_count": settings_obj.stateful_step_count,
            "deadline": settings_obj.deadline,
        }
    return resolved


def run_budget(name: str, *, nightly: bool, timeout: int) -> dict[str, Any]:
    """Run the vendored stateful suite once at the given budget."""
    env_extra = {
        "FWF_NIGHTLY_KERNEL_TESTS": "1",
        "FWF_NIGHTLY": "1" if nightly else "",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    cmd = [
        sys.executable,
        "-W",
        "ignore",
        "-m",
        "pytest",
        SUITE,
        "-q",
        "--tb=no",
        "-rA",
        "-p",
        "no:cacheprovider",
    ]

    import os

    env = dict(os.environ)
    for key, value in env_extra.items():
        if value:
            env[key] = value
        else:
            env.pop(key, None)

    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    elapsed = time.time() - started
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    passed = _parse_passed(proc.stdout)
    return {
        "budget": name,
        "max_examples": REQUIRED_NIGHTLY_EXAMPLES if nightly else None,
        "command": " ".join(cmd),
        "exit_code": proc.returncode,
        "passed": passed,
        "summary_line": tail,
        "elapsed_seconds": round(elapsed, 1),
        "result": "PASS" if proc.returncode == 0 and passed is not None else "FAIL",
        "stdout_tail": proc.stdout.strip()[-4000:],
        "stderr_tail": proc.stderr.strip()[-2000:],
    }


def _parse_passed(stdout: str) -> int | None:
    """Count the ``PASSED`` lines ``-rA`` emits (one per collected test).

    Under ``-q`` pytest prints progress dots and, for an all-passing run, *no*
    "N passed" summary at all, so a regex on the summary line silently returns
    ``None`` for a perfectly green run. Counting the per-test report lines that
    ``-rA`` adds is robust to that, and to any future pytest output change.
    """
    import re

    return len(re.findall(r"^PASSED ", stdout, flags=re.MULTILINE))


def build_report(
    vendor,
    fast: dict[str, Any],
    nightly: dict[str, Any],
    fast_budgets: dict[str, dict],
    nightly_budgets: dict[str, dict],
) -> dict[str, Any]:
    # Under the nightly profile every machine must sit at exactly 1000 examples
    # and must keep its fast step count and its deadline exemption.
    expected_nightly = all(
        b["max_examples"] == REQUIRED_NIGHTLY_EXAMPLES
        and b["stateful_step_count"] == step_count
        and b["deadline"] is None
        for name, _fast, step_count in MACHINES
        for b in [nightly_budgets[name]]
    )
    # Under the fast profile the committed per-machine budgets must be intact.
    expected_fast = all(
        b["max_examples"] == fast_examples and b["stateful_step_count"] == step_count
        for name, fast_examples, step_count in MACHINES
        for b in [fast_budgets[name]]
    )
    ok = (
        fast["result"] in ("PASS", "SKIPPED")
        and nightly["result"] == "PASS"
        and expected_fast
        and expected_nightly
    )
    return {
        "schema_version": 1,
        "gate": "M5.2",
        "title": "Hypothesis nightly stateful profile (max_examples=1000)",
        "timestamp": datetime.now(UTC).isoformat(),
        "python_version": platform.python_version(),
        "hypothesis_version": _pkg_version("hypothesis"),
        "pytest_version": _pkg_version("pytest"),
        "vendor_integrity": {
            "source_commit": vendor["source_commit"],
            "files_verified": vendor["files_verified"],
            "all_match": vendor["all_match"],
        },
        "suite": SUITE,
        "machines": {name: {"fast_max_examples": f, "stateful_step_count": s} for name, f, s in MACHINES},
        "fast_budget": fast_budgets,
        "nightly_budget": nightly_budgets,
        "required_nightly_examples": REQUIRED_NIGHTLY_EXAMPLES,
        "budgets_as_expected": expected_fast and expected_nightly,
        "fast_run": fast,
        "nightly_run": nightly,
        "result": "PASS" if ok else "FAIL",
    }


def _pkg_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:  # pragma: no cover
        return "not installed"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M5.2 nightly stateful gate")
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument(
        "--skip-fast",
        action="store_true",
        help="Only run the 1000-example nightly budget (used by the nightly workflow).",
    )
    args = parser.parse_args(argv)

    try:
        vendor = verify_all()
    except VendorDrift as exc:
        print(f"VENDOR DRIFT: {exc}", file=sys.stderr)
        return 2

    import os

    fast_budgets = _resolved_budgets(nightly=False)
    if args.skip_fast:
        fast = {
            "budget": "fast",
            "skipped": True,
            "result": "SKIPPED",
            "passed": None,
            "exit_code": 0,
            "elapsed_seconds": 0.0,
        }
    else:
        fast = run_budget("fast", nightly=False, timeout=args.timeout)
    os.environ["FWF_NIGHTLY"] = "1"
    nightly_budgets = _resolved_budgets(nightly=True)
    del os.environ["FWF_NIGHTLY"]
    nightly = run_budget("nightly", nightly=True, timeout=args.timeout)

    report = build_report(vendor, fast, nightly, fast_budgets, nightly_budgets)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    out = ARTIFACTS / "stateful_report.json"
    out.write_text(json.dumps(report, indent=2) + "\n")

    print(
        f"M5.2 {report['result']}: fast={fast['result']} nightly={nightly['result']} "
        f"({nightly['passed']} passed @ {REQUIRED_NIGHTLY_EXAMPLES} examples, "
        f"{nightly['elapsed_seconds']}s) -> {out}"
    )
    if report["result"] != "PASS":
        print(nightly.get("stdout_tail", "")[-2000:], file=sys.stderr)
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
