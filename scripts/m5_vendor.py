#!/usr/bin/env python3
"""Byte-level integrity verification for the M5 vendored Financial System Core surfaces.

Milestone 5 makes two independent claims about the vendored FWF kernel:

* **M5.1** the independent ``python-accounting`` oracle agrees with the *shipped*
  kernel snapshot, so the bytes in ``app/_vendor/fwf_kernel`` are what was
  proven correct; and
* **M5.2/M5.3** the canonical kernel test surface and the mutation sandbox run
  against that same snapshot, so the bytes there are what was proven well-tested.

Both claims are worthless if a vendored file drifted, so every M5 gate
re-verifies the manifests *before* it measures anything and fails closed. This
is the single implementation the oracle, stateful and mutation runners share.

A manifest is a ``SOURCE.json`` with a ``source_commit``, a list of
``source_paths`` relative to the canonical repository, and a ``sha256`` map from
those paths to digests. The local snapshot is laid out flat (subdirectories are
preserved relative to the manifest's own common source prefix), so a vendored
file is located by stripping the longest common leading directory shared by all
of the manifest's source paths.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

REPO = Path(__file__).resolve().parents[1]

#: The two independent vendor groups M5 pins, in the order they are reported.
MANIFESTS: tuple[Path, ...] = (
    REPO / "app" / "_vendor" / "fwf_kernel" / "SOURCE.json",
    REPO / "tests" / "m5" / "kernel_stateful" / "SOURCE.json",
)


class VendorDrift(RuntimeError):
    """Raised when a vendored file is missing or no longer matches its digest."""


def _local_name(source_path: str, all_paths: Sequence[str]) -> str:
    """Map a canonical source path onto its path inside the local snapshot."""
    paths = [PurePosixPath(p) for p in all_paths]
    prefix = os.path.commonpath([str(p) for p in paths])
    full = PurePosixPath(source_path)
    try:
        tail = full.relative_to(prefix)
    except ValueError:  # pragma: no cover - manifests are internally consistent
        tail = PurePosixPath(full.name)
    return str(tail) if str(tail) != "." else full.name


def verify_manifest(manifest_path: Path) -> dict[str, Any]:
    """Re-verify one manifest. Raises :class:`VendorDrift` on any drift."""
    if not manifest_path.exists():
        raise VendorDrift(f"manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    digests: dict[str, str] = manifest["sha256"]
    source_paths = list(digests)
    # Guard against a manifest that silently stopped describing the directory.
    if "source_paths" in manifest and sorted(manifest["source_paths"]) != sorted(source_paths):
        raise VendorDrift(
            f"{manifest_path.name}: source_paths and sha256 keys disagree; "
            "the manifest is internally inconsistent"
        )

    checked: dict[str, str] = {}
    for source_path, expected in digests.items():
        local = manifest_path.parent / _local_name(source_path, source_paths)
        if not local.exists():
            raise VendorDrift(f"vendored file missing: {source_path} (expected at {local})")
        actual = hashlib.sha256(local.read_bytes()).hexdigest()
        if actual != expected:
            raise VendorDrift(
                f"vendored file drifted: {source_path}\n"
                f"  expected {expected}\n"
                f"  actual   {actual}\n"
                f"  at       {local}"
            )
        checked[source_path] = actual
    return {
        "manifest": str(manifest_path.relative_to(REPO)),
        "source_repository": manifest.get("source_repository", ""),
        "source_commit": manifest["source_commit"],
        "files_verified": len(checked),
        "all_match": True,
        "sha256": checked,
    }


def verify_all() -> dict[str, Any]:
    """Verify every M5 vendor manifest.

    Raises :class:`VendorDrift` on the first problem, because a partially
    verified snapshot is not a verified snapshot.
    """
    reports = [verify_manifest(path) for path in MANIFESTS]
    commits = {r["source_commit"] for r in reports}
    if len(commits) != 1:
        raise VendorDrift(
            "the two M5 vendor groups disagree on source_commit: "
            + ", ".join(f"{r['manifest']}={r['source_commit']}" for r in reports)
        )
    return {
        "source_commit": reports[0]["source_commit"],
        "groups": reports,
        "files_verified": sum(r["files_verified"] for r in reports),
        "all_match": True,
    }


if __name__ == "__main__":  # pragma: no cover - manual integrity check
    summary = verify_all()
    print(
        f"vendor integrity OK: {summary['files_verified']} files across "
        f"{len(summary['groups'])} groups @ {summary['source_commit']}"
    )
    for group in summary["groups"]:
        print(f"  {group['manifest']}: {group['files_verified']} files")
