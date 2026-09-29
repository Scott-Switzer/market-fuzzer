"""Bootstrap for the M5.3 survivor killing tests.

These tests are Market Fuzzer-authored, unlike ``tests/m5/kernel_stateful``,
which is the byte-pinned canonical Financial System Core surface. They live in
their own directory for that reason: M5.3 finds gaps in the canonical suite, and
closing them must never mean editing the canonical bytes.

They still import the kernel as the top-level package ``fwf_kernel``, for the
same reason the canonical suite does, so this conftest resolves that name in both
layouts the M5.3 runner uses: the repository (``app/_vendor`` on ``sys.path``)
and the mutation sandbox (the kernel is the top-level package). ``FWF_VENDOR_ROOT``
overrides both.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: Repository-root equivalent of this directory, i.e. the parent of ``tests``.
ROOT = HERE.parents[2]


def _vendor_dir() -> Path:
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
