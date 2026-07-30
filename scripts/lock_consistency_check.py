#!/usr/bin/env python3
"""Lock consistency gate (reset brief section 2) without a live-PyPI race.

`pip-compile ... | git diff` is not a stable CI gate: pip-tools resolves against
live PyPI, so a package released between commit time and CI time makes a
byte-for-byte diff fail even though nothing in the repo changed. That is a flaky
gate, not a correctness gate.

Instead we verify what actually matters:

  1. requirements.lock installs cleanly under --require-hashes (proven by the
     job that installs it), and
  2. every top-level dependency declared in pyproject.toml (project deps + the
     `dev` extra) is present in the lock at a version that SATISFIES its declared
     specifier.

If pyproject adds/changes/removes a dependency without re-locking, (2) fails.
Transient upstream releases do not.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "requirements.lock"

# Lock-only tooling extra is intentionally excluded from the compiled graph.
EXCLUDED_EXTRAS = {"lock"}


def _lock_versions(text: str) -> dict[str, Version]:
    versions: dict[str, Version] = {}
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;]+)", line)
        if m:
            name = m.group(1).lower().replace("_", "-")
            try:
                versions[name] = Version(m.group(2))
            except Exception:
                pass
    return versions


def main() -> int:
    data = tomllib.loads(PYPROJECT.read_text())
    project = data.get("project", {})
    reqs: list[str] = list(project.get("dependencies", []))
    for extra, deps in (project.get("optional-dependencies", {}) or {}).items():
        if extra in EXCLUDED_EXTRAS:
            continue
        reqs.extend(deps)

    lock = _lock_versions(LOCK.read_text())
    if not lock:
        print("ERROR: requirements.lock has no pinned packages", file=sys.stderr)
        return 2

    problems: list[str] = []
    for raw in reqs:
        req = Requirement(raw)
        name = req.name.lower().replace("_", "-")
        if name not in lock:
            problems.append(f"  - {req.name}: declared in pyproject but MISSING from lock")
            continue
        ver = lock[name]
        if req.specifier and ver not in req.specifier:
            problems.append(f"  - {req.name}: locked {ver} does not satisfy '{req.specifier}'")

    if problems:
        print("Lock inconsistent with pyproject.toml:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        print(
            "\nRun `make lock` and commit requirements.lock.",
            file=sys.stderr,
        )
        return 1

    print(f"LOCK CONSISTENCY: PASS ({len(reqs)} declared deps satisfied by lock)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
