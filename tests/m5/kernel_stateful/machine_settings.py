"""Milestone 5.2: the nightly stateful profile.

The three canonical accounting state machines -- ``LedgerMachine``,
``OperationalBookMachine`` and ``EquityMachine`` -- keep their fast, per-PR
example budgets by default. The nightly profile raises *only* ``max_examples``
to 1000 for the same machines, the same rules, the same preconditions and the
same invariants.

This module is the single place where the budget lives, so the fast default and
the nightly profile can never drift apart.

M5 never:

* creates a second or weaker copy of a machine;
* lowers ``stateful_step_count``;
* removes a precondition;
* suppresses a health check;
* adds an example to an ignore list.

A failing example is a real finding. Shrink it, keep the reproducer, fix the
root cause in production or test code, and re-run the full 1000-example profile.
"""

from __future__ import annotations

import os

from hypothesis import settings

#: Environment variable that switches the machines from the fast per-PR budget
#: to the M5 nightly budget.
NIGHTLY_ENV = "FWF_NIGHTLY"

#: ``max_examples`` used by ``uv run pytest`` and by PR CI.
FAST_MAX_EXAMPLES = 60

#: ``max_examples`` for the M5.2 nightly stateful profile.
NIGHTLY_MAX_EXAMPLES = 1000


def nightly_enabled() -> bool:
    """Return ``True`` when the M5 nightly stateful profile is requested."""
    return os.environ.get(NIGHTLY_ENV) == "1"


def examples(default: int) -> int:
    """Return the example budget for ``default`` under the active profile."""
    return NIGHTLY_MAX_EXAMPLES if nightly_enabled() else default


def machine_settings(*, max_examples: int, stateful_step_count: int) -> object:
    """Return Hypothesis settings for an accounting state machine.

    ``stateful_step_count`` and ``deadline=None`` are passed through unchanged
    in both profiles; only ``max_examples`` varies.
    """
    return settings(
        max_examples=examples(max_examples),
        stateful_step_count=stateful_step_count,
        deadline=None,
    )
