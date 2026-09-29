"""Bootstrap for the vendored canonical FWF kernel test surface.

The files in this directory are byte-for-byte copies of the Financial System
Core kernel test suite, pinned in ``SOURCE.json`` next to them. They import the
kernel as the top-level package ``fwf_kernel`` because that is how the canonical
suite imports it, so this conftest is what makes the Market Fuzzer vendor
snapshot resolvable under exactly that name. The nightly therefore runs *the
canonical bytes* against *the vendored kernel bytes* rather than a paraphrase of
either.

The same conftest also serves the M5 mutation sandbox
(``scripts/m5_mutation.py``), which copies the vendored kernel to a scratch
directory where it is the top-level ``fwf_kernel`` package. Both layouts are
resolved here so a single bootstrap serves the stateful profile and the
mutation run.

These canonical tests are excluded from the fast PR gate on purpose. They are
the M5 nightly stateful profile (M5.2) and the M5 mutation killing suite
(M5.3); running them at their fast budgets on every PR would duplicate
``financial-system-core``'s own gate. Set ``FWF_NIGHTLY_KERNEL_TESTS=1`` to
collect them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent

#: Repository-root equivalent of this directory, i.e. the parent of ``tests``.
ROOT = HERE.parents[2]

#: Environment variable that opts the vendored canonical test surface into the
#: current pytest collection.
NIGHTLY_ENV = "FWF_NIGHTLY_KERNEL_TESTS"


def _vendor_dir() -> Path:
    """Return the directory that must be on ``sys.path`` for ``fwf_kernel``.

    Two layouts exist and both must resolve:

    * the repository, where the kernel snapshot is at ``app/_vendor/fwf_kernel``
      and so is imported by putting ``app/_vendor`` on ``sys.path``;
    * the mutation sandbox, where the kernel snapshot *is* the top-level
      ``fwf_kernel`` package and the sandbox root goes on ``sys.path``.

    ``FWF_VENDOR_ROOT`` overrides both.
    """
    override = os.environ.get("FWF_VENDOR_ROOT")
    if override:
        return Path(override).resolve()
    in_repo = ROOT / "app" / "_vendor"
    if in_repo.is_dir():
        return in_repo
    return ROOT


VENDOR = _vendor_dir()

if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

if os.environ.get(NIGHTLY_ENV) != "1":
    collect_ignore_glob = ["*.py"]
