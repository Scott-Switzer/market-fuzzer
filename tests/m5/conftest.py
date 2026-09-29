"""Collection guards for the M5 package.

The M5.1 oracle imports ``python_accounting`` and the M5.3 runner imports
``mutmut``; both are verification-only dependencies that live in the isolated
``.venv-m5`` environment (see ``requirements-m5.txt``) and deliberately never
become Market Fuzzer runtime dependencies. When a module's dependency is
absent -- as in the application's own dev environment, which has the app
graph but not the M5 tooling -- the affected test modules are skipped at
collection instead of erroring, so a plain ``pytest`` run stays green.
"""

from __future__ import annotations

import importlib.util

import pytest

if importlib.util.find_spec("python_accounting") is None:
    collect_ignore = [
        "test_planted_defects.py",
        "test_m5_gates.py",
    ]

else:
    collect_ignore = []


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """Skip the heavy gate tests when the M5 tooling environment is absent."""
    if importlib.util.find_spec("python_accounting") is None:
        skip_m5 = pytest.mark.skip(reason="python_accounting not installed (M5 tooling env)")
        for item in items:
            if item.fspath and "m5" in str(item.fspath):
                item.add_marker(skip_m5)
