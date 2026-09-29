#!/usr/bin/env python3
"""M5.3 mutation hardening for the vendored FWF accounting kernel.

Runs ``mutmut`` against the accounting-relevant kernel modules that Market Fuzzer
actually ships, and writes ``artifacts/m5/mutation_survivors.json``.

Why a sandbox
-------------
The canonical FWF kernel test suite imports the kernel as the top-level package
``fwf_kernel``; mutmut derives its mutant keys from the *file path* of the source
it mutates. Pointing mutmut at ``app/_vendor/fwf_kernel`` would therefore produce
keys like ``app._vendor.fwf_kernel.equity.x_foo`` while the canonical tests
exercise ``fwf_kernel.equity.x_foo``, and mutmut would stop early with a
false "cannot see the module" report.

So the runner materialises a scratch tree in which the vendored kernel *is* the
top-level ``fwf_kernel`` package. The bytes are copies of the shipped snapshot,
and their SHA-256 digests are re-verified against
``app/_vendor/fwf_kernel/SOURCE.json`` immediately before the run, so mutation
testing provably exercises the real kernel content rather than a paraphrase.

Killing suite
-------------
The canonical Financial System Core kernel test surface, vendored byte-for-byte
at ``tests/m5/kernel_stateful`` (see its ``SOURCE.json``). The differential oracle
is deliberately not part of it: a hundred sequences cost far more per candidate
than a mutant budget can afford, and proving cross-implementation agreement is
the oracle's job, not a mutant's.

Every surviving mutant must be triaged into exactly one of
``REAL_TEST_GAP`` / ``EQUIVALENT_MUTANT`` / ``UNREACHABLE_DEFENSIVE_CODE`` /
``OUT_OF_M5_SCOPE`` by ``--triage`` before this script will report PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SANDBOX = REPO / ".m5-mutation"
ARTIFACTS = REPO / "artifacts" / "m5"
TRIAGE = REPO / "tests" / "m5" / "mutation_triage.json"

#: The M5.3 primary accounting targets.
PRIMARY_TARGETS: tuple[str, ...] = (
    "ledger.py",
    "subledgers.py",
    "equity.py",
    "filings.py",
)

#: ``temporal.py`` is mutated only for the accounting / point-in-time semantics
#: this milestone exercises, and is reported as its own target so its survivors
#: are triaged separately from the primary accounting targets.
TEMPORAL_TARGETS: tuple[str, ...] = ("temporal.py",)

#: The only four survivor classifications M5 accepts.
CLASSIFICATIONS: tuple[str, ...] = (
    "REAL_TEST_GAP",
    "EQUIVALENT_MUTANT",
    "UNREACHABLE_DEFENSIVE_CODE",
    "OUT_OF_M5_SCOPE",
)

SANDBOX_PYPROJECT = """\
[tool.mutmut]
source_paths = ["fwf_kernel"]
only_mutate = [{only_mutate}]
pytest_add_cli_args_test_selection = ["tests/m5/kernel_stateful", "tests/m5/mutation_kills"]
pytest_add_cli_args = ["-p", "no:cacheprovider", "-x", "--no-header", "-qq"]
timeout_multiplier = 8.0
timeout_constant = 60.0
use_git_change_detection = false
mutate_only_covered_lines = false

[tool.pytest.ini_options]
addopts = ""
"""


class VendorDrift(RuntimeError):
    """Raised when the vendored kernel no longer matches its recorded hashes."""


def verify_vendor() -> dict[str, Any]:
    """Re-verify every pinned vendored kernel file. Raises on any drift."""
    kernel_dir = REPO / "app" / "_vendor" / "fwf_kernel"
    source = json.loads((kernel_dir / "SOURCE.json").read_text())
    checked: dict[str, str] = {}
    for rel, expected in source["sha256"].items():
        path = kernel_dir / Path(rel).name
        if not path.exists():
            raise VendorDrift(f"vendored file missing: {rel}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise VendorDrift(f"vendored file drifted: {rel}\n  expected {expected}\n  actual   {actual}")
        checked[rel] = actual
    return {
        "source_commit": source["source_commit"],
        "files_verified": len(checked),
        "all_match": True,
        "sha256": checked,
    }


def build_sandbox(targets: Sequence[str], *, keep_results: bool = False) -> Path:
    """Create the mutmut sandbox and return its path.

    ``keep_results`` preserves an existing ``mutants/`` directory across the
    rebuild, which is what ``--rebuild-report`` needs: the sandbox content is
    re-materialised from the pinned snapshot so it provably still matches the
    vendor, while the per-mutant statuses from the previous run are retained.
    """
    preserved: Path | None = None
    if keep_results and (SANDBOX / "mutants").exists():
        preserved = SANDBOX.with_name(SANDBOX.name + "-mutants-keep")
        if preserved.exists():
            shutil.rmtree(preserved)
        shutil.move(str(SANDBOX / "mutants"), str(preserved))
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    (SANDBOX / "tests" / "m5").mkdir(parents=True)
    shutil.copytree(
        REPO / "app" / "_vendor" / "fwf_kernel",
        SANDBOX / "fwf_kernel",
    )
    # SOURCE.json is not Python; drop it from the mutated package.
    (SANDBOX / "fwf_kernel" / "SOURCE.json").unlink()
    shutil.copytree(
        REPO / "tests" / "m5" / "kernel_stateful",
        SANDBOX / "tests" / "m5" / "kernel_stateful",
    )
    # M5's own killing tests. The canonical surface above is byte-pinned to
    # financial-system-core and must never be edited here, so every test written
    # in response to an M5.3 survivor lives in this separate, Market Fuzzer-owned
    # directory and is collected alongside the canonical bytes.
    kills = REPO / "tests" / "m5" / "mutation_kills"
    if kills.is_dir():
        shutil.copytree(
            kills,
            SANDBOX / "tests" / "m5" / "mutation_kills",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    only_mutate = ", ".join(f'"fwf_kernel/{name}"' for name in targets)
    (SANDBOX / "pyproject.toml").write_text(SANDBOX_PYPROJECT.format(only_mutate=only_mutate))
    if preserved is not None:
        shutil.move(str(preserved), str(SANDBOX / "mutants"))
        preserved.parent.mkdir(parents=True, exist_ok=True)
    return SANDBOX


#: mutmut exit code -> survivor classification bucket. Anything that is not an
#: ordinary ``killed``/``survived`` result is surfaced separately, because an
#: unexplained timeout or suspicious result is a M5 hard-stop condition.
EXIT_STATUS: dict[int, str] = {
    0: "survived",
    1: "killed",
    3: "killed",
    33: "uncovered",
    34: "skipped",
    35: "suspicious",
    36: "timeout",
    37: "caught_by_type_check",
}
HARD_STOP_STATUSES = ("timeout", "suspicious", "segfault", "no tests")


def _retry_timeouts(mutants_dir: Path, results: dict[str, str], *, timeout: int) -> list[dict[str, Any]]:
    """Re-run each timed-out mutant on its own and record the verdict.

    A mutmut timeout is ambiguous: it can mean the killing suite failed to notice
    a real behavioural change, or it can mean the machine was too loaded for one
    test run to finish inside a budget derived from a *baseline* run that was
    itself measured under that same load. Conflating the two would either fail
    the gate for nothing or, worse, wave away a genuine gap.

    So timeouts are retried serially -- one mutant, no parallelism, a much larger
    budget -- and only a timeout that *survives* an isolated re-run is reported as
    a real finding. Every retry, whatever it finds, is recorded.
    """

    suspects = sorted(name for name, status in results.items() if status == "timeout")
    if not suspects:
        return []

    env = dict(os.environ)
    env["FWF_NIGHTLY_KERNEL_TESTS"] = "1"
    env.pop("FWF_VENDOR_ROOT", None)
    env.pop("FWF_NIGHTLY", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    verdicts: list[dict[str, Any]] = []
    for name in suspects:
        started = time.time()
        try:
            subprocess.run(
                [sys.executable, "-W", "ignore", "-m", "mutmut", "run", name],
                cwd=SANDBOX,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            results[name] = "timeout"
            verdicts.append(
                {
                    "mutant": name,
                    "isolated_status": "timeout",
                    "confirmed": True,
                    "elapsed_seconds": round(time.time() - started, 1),
                }
            )
            continue

        # mutmut rewrites the .meta for the file it re-ran; trust the file, not
        # the exit code, so the re-run's own classification is what counts.
        refreshed = _read_results(mutants_dir)
        status = refreshed.get(name, "timeout")
        results[name] = status
        verdicts.append(
            {
                "mutant": name,
                "isolated_status": status,
                "confirmed": status == "timeout",
                "elapsed_seconds": round(time.time() - started, 1),
            }
        )
    return verdicts


def _read_results(mutants_dir: Path) -> dict[str, str]:
    """Map every mutant key to its status from mutmut's per-file ``.meta`` files."""
    from mutmut.stats import status_by_exit_code

    results: dict[str, str] = {}
    for meta in sorted(mutants_dir.glob("fwf_kernel/*.meta")):
        payload = json.loads(meta.read_text())
        for key, exit_code in payload["exit_code_by_key"].items():
            results[key] = status_by_exit_code[exit_code]
    return results


def _mutant_path(name: str) -> Path:
    """Return the sandbox-relative source path that contains ``name``.

    mutmut's own ``path`` argument is the *original* source path relative to
    where it runs (``fwf_kernel/equity.py``); the mutated copy and the line-span
    index are both looked up by mutmut under a ``mutants/`` prefix of its own.
    A mutant key encodes the module as its dotted prefix, terminated by the
    ``x_``/``x<class-sep>`` marker, so mutmut's helpers are used rather than
    hand-parsed string surgery.
    """
    from mutmut.utils.format_utils import get_module_from_key

    return Path(*get_module_from_key(name).split(".")).with_suffix(".py")


def _describe(name: str) -> dict[str, str]:
    """Split a mutant key into its module, class and function components."""
    from mutmut.utils.format_utils import get_module_from_key, parse_mutant_key

    key = name.partition("__mutmut_")[0]
    function, class_name = parse_mutant_key(key.rpartition(".")[2])
    return {
        "module": get_module_from_key(name),
        "class": class_name or "",
        "function": function,
    }


def _mutant_diffs(mutants_dir: Path, names: Sequence[str]) -> dict[str, str]:
    """Render mutmut's own unified diff for each named mutant."""
    from mutmut.mutation.diff_apply import get_diff_for_mutant
    from mutmut.utils.file_utils import change_cwd

    diffs: dict[str, str] = {}
    sandbox = mutants_dir.parent
    with change_cwd(sandbox):
        for name in names:
            try:
                diffs[name] = get_diff_for_mutant(name, path=_mutant_path(name))
            except Exception as exc:  # pragma: no cover - diagnostics only
                diffs[name] = f"<diff unavailable: {exc}>"
    return diffs


def _summarize(
    mutants_dir: Path,
    results: dict[str, str],
    exit_code: int,
    elapsed: float,
    timeout_retries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Turn raw per-mutant statuses into the report's counts and survivors."""
    counts: dict[str, int] = {}
    for status in results.values():
        counts[status] = counts.get(status, 0) + 1

    surviving = sorted(name for name, status in results.items() if status == "survived")
    diffs = _mutant_diffs(mutants_dir, surviving)

    return {
        "exit_code": exit_code,
        "elapsed_seconds": round(elapsed, 1),
        "timeout_retries": timeout_retries or [],
        "generated": len(results),
        "killed": counts.get("killed", 0),
        "survived": counts.get("survived", 0),
        "uncovered": counts.get("uncovered", 0),
        "timeouts": counts.get("timeout", 0),
        "suspicious": counts.get("suspicious", 0),
        "skipped": counts.get("skipped", 0),
        "statuses": dict(sorted(counts.items())),
        "non_killed": [
            {"mutant": name, **_describe(name), "status": status}
            for name, status in sorted(results.items())
            if status not in ("killed", "survived")
        ],
        "survivors": [{**_describe(name), "mutant": name, "diff": diffs.get(name, "")} for name in surviving],
        "log": str((SANDBOX / "mutmut-run.log").relative_to(REPO)),
    }


def run_mutmut(targets: Sequence[str], *, children: int, timeout: int) -> dict[str, Any]:
    """Run mutmut in the sandbox and return raw counts plus survivors."""
    build_sandbox(targets)
    env = dict(os.environ)
    env["FWF_NIGHTLY_KERNEL_TESTS"] = "1"
    # Do NOT set FWF_VENDOR_ROOT here: mutmut runs pytest from inside
    # `mutants/`, and the vendored bootstrap must resolve to `mutants/fwf_kernel`
    # (the mutated copy), not to the pristine sandbox copy.
    env.pop("FWF_VENDOR_ROOT", None)
    env.pop("FWF_NIGHTLY", None)  # mutation runs at the fast budget by design
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    started = time.time()
    proc = subprocess.run(
        [sys.executable, "-W", "ignore", "-m", "mutmut", "run", "--max-children", str(children)],
        cwd=SANDBOX,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    elapsed = time.time() - started
    (SANDBOX / "mutmut-run.log").write_text(proc.stdout + "\n" + proc.stderr)
    mutants_dir = SANDBOX / "mutants"
    results = _read_results(mutants_dir)
    retries = _retry_timeouts(mutants_dir, results, timeout=timeout)
    return _summarize(mutants_dir, results, proc.returncode, elapsed, retries)


def rebuild_report(targets: Sequence[str]) -> dict[str, Any]:
    """Re-derive the report from an existing sandbox without re-running mutmut.

    Triaging survivors means iterating on ``mutation_triage.json`` and, often, on
    the killing tests -- neither of which should cost another full mutant sweep
    just to re-read statuses. The sandbox is rebuilt byte-for-byte from the
    pinned snapshot first, so the `.meta` files being re-read still describe the
    current vendor content and the diffs are regenerated against it.
    """
    sandbox = build_sandbox(targets, keep_results=True)
    mutants_dir = sandbox / "mutants"
    if not (mutants_dir / "pyproject.toml").exists():
        raise FileNotFoundError(f"no mutmut results in {mutants_dir}; run without --rebuild-report first")
    stats_path = mutants_dir / "mutmut-stats.json"
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else {}
    results = _read_results(mutants_dir)
    if not results:
        raise FileNotFoundError(f"no mutant results found in {mutants_dir}")
    elapsed = float(stats.get("time", 0.0) or 0.0)
    return _summarize(mutants_dir, results, 0, elapsed)


def load_triage() -> dict[str, Any]:
    if TRIAGE.exists():
        return json.loads(TRIAGE.read_text())
    return {"schema_version": 1, "mutants": {}}


def build_report(targets: Sequence[str], raw: dict[str, Any], vendor: dict[str, Any]) -> dict[str, Any]:
    triage = load_triage()
    entries = []
    untriaged: list[str] = []
    for survivor in raw["survivors"]:
        record = triage["mutants"].get(survivor["mutant"], {})
        classification = record.get("classification")
        entry = {
            **survivor,
            "classification": classification or "UNTRIAGED",
            "rationale": record.get("rationale", ""),
            "test_added": record.get("test_added", ""),
        }
        if classification not in CLASSIFICATIONS:
            untriaged.append(survivor["mutant"])
        entries.append(entry)

    by_class: dict[str, int] = dict.fromkeys(CLASSIFICATIONS, 0)
    for entry in entries:
        if entry["classification"] in by_class:
            by_class[entry["classification"]] += 1

    real_gap_survivors = [e["mutant"] for e in entries if e["classification"] == "REAL_TEST_GAP"]
    ok = (
        raw["exit_code"] == 0
        and not untriaged
        and not real_gap_survivors
        and raw["timeouts"] == 0
        and raw["suspicious"] == 0
        and raw["skipped"] == 0
    )

    try:
        import mutmut

        mutmut_version = mutmut.__version__
    except Exception:  # pragma: no cover - version metadata is best effort
        mutmut_version = "3.8.0"

    return {
        "schema_version": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "mutmut_version": mutmut_version,
        "python_version": sys.version.split()[0],
        "vendor_source_sha": vendor["source_commit"],
        "vendor_hash_integrity": "ALL_MATCH" if vendor["all_match"] else "DRIFT",
        "targets": [f"app/_vendor/fwf_kernel/{name}" for name in targets],
        "generated_mutants": raw["generated"],
        "killed": raw["killed"],
        "survived": raw["survived"],
        "uncovered": raw["uncovered"],
        "timeouts": raw["timeouts"],
        "suspicious": raw["suspicious"],
        "skipped": raw["skipped"],
        "statuses": raw["statuses"],
        "elapsed_seconds": raw["elapsed_seconds"],
        "timeout_retries": raw.get("timeout_retries", []),
        "non_killed": raw.get("non_killed", []),
        "killing_suite": (
            "tests/m5/kernel_stateful (vendored canonical FWF kernel test surface) "
            "+ tests/m5/mutation_kills (M5.3 survivor killing tests)"
        ),
        "survivors": entries,
        "classification": by_class,
        "untriaged_survivors": untriaged,
        "real_test_gap_survivors": real_gap_survivors,
        "result": "PASS" if ok else "FAIL",
        "run_log": raw["log"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--targets",
        default="primary",
        choices=["primary", "temporal", "all"],
        help="Which accounting targets to mutate.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="FILE.py",
        help=(
            "Restrict the sweep to these files, intersected with --targets. Used to "
            "iterate on one target's survivors without paying for the other three."
        ),
    )
    parser.add_argument("--children", type=int, default=max(2, (os.cpu_count() or 4)))
    parser.add_argument("--timeout", type=int, default=14400)
    parser.add_argument(
        "--rebuild-report",
        action="store_true",
        help=(
            "Re-derive the report from the previous run's results in the sandbox "
            "without re-running mutmut. Use while iterating on survivor triage."
        ),
    )
    args = parser.parse_args(argv)

    targets: tuple[str, ...]
    if args.targets == "primary":
        targets = PRIMARY_TARGETS
    elif args.targets == "temporal":
        targets = TEMPORAL_TARGETS
    else:
        targets = PRIMARY_TARGETS + TEMPORAL_TARGETS

    if args.only:
        wanted = {name if name.endswith(".py") else f"{name}.py" for name in args.only}
        unknown = wanted - set(targets)
        if unknown:
            print(f"NOT IN THIS TARGET SET: {sorted(unknown)}", file=sys.stderr)
            return 2
        targets = tuple(t for t in targets if t in wanted)
        if not targets:
            print("EMPTY TARGET SELECTION", file=sys.stderr)
            return 2

    try:
        vendor = verify_vendor()
    except VendorDrift as exc:
        print(f"VENDOR DRIFT: {exc}", file=sys.stderr)
        return 2

    raw = (
        rebuild_report(targets)
        if args.rebuild_report
        else run_mutmut(targets, children=args.children, timeout=args.timeout)
    )
    report = build_report(targets, raw, vendor)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    out = ARTIFACTS / "mutation_survivors.json"
    out.write_text(json.dumps(report, indent=2) + "\n")

    print(
        f"mutmut {report['result']}: generated={report['generated_mutants']} "
        f"killed={report['killed']} survived={report['survived']} "
        f"uncovered={report['uncovered']} timeouts={report['timeouts']} "
        f"untriaged={len(report['untriaged_survivors'])} "
        f"real_test_gap={len(report['real_test_gap_survivors'])} -> {out}"
    )
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
