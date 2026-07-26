"""Strategy executor contracts (reset brief Phase 2 item 15).

These dataclasses/protocols define the boundary between *strategy logic* (which
turns a StrategySpec + market data into target weights) and the *generic
portfolio accounting simulator* (which turns targets into fills, cash, shares,
costs, and an equity curve).

Executors decide TARGET WEIGHTS at the end of each decision bar. They may NOT
touch cash, shares, fills, commissions, or borrow accounting -- that is the
accounting engine's sole responsibility. This separation is what makes "60/40"
and "long/short momentum" run through the same accounting path while producing
completely different trades.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np

    from app.domain.strategy_spec import StrategySpec, StrategyType


@dataclass(frozen=True)
class ValidationIssue:
    """A structured, decision-focused problem with a spec in a data context."""

    code: str
    message: str
    severity: str = "error"  # "error" blocks execution; "warning" is advisory
    field: str | None = None


@dataclass(frozen=True)
class StrategyExecutionContext:
    """Everything an executor needs to compute targets -- and nothing else.

    Arrays are aligned: ``dates`` is length T, ``assets`` is length N, and the
    price matrices are T x N. ``benchmark_close`` is length T (independent series).
    ``eligibility_mask`` is T x N booleans (e.g. survivorship / listing / data
    availability). ``data_provenance`` records where the data came from for
    evidence.
    """

    dates: np.ndarray
    assets: tuple[str, ...]
    open: np.ndarray
    close: np.ndarray
    benchmark_close: np.ndarray | None = None
    eligibility_mask: np.ndarray | None = None
    data_provenance: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TargetPlan:
    """The output of an executor: desired portfolio weights over time.

    ``target_weights`` is T x N; row t holds the weights DECIDED at the close of
    decision bar t. The accounting engine executes those targets at the next
    valid open. ``rebalance_mask`` (length T) marks which bars are decision bars.
    """

    strategy_hash: str
    dates: np.ndarray
    assets: tuple[str, ...]
    target_weights: np.ndarray
    rebalance_mask: np.ndarray
    diagnostics: dict[str, object] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    provenance: dict[str, str] = field(default_factory=dict)


class StrategyExecutor(Protocol):
    """A concrete, executable strategy family."""

    strategy_type: StrategyType

    def validate_spec(
        self,
        spec: StrategySpec,
        context: StrategyExecutionContext | None = None,
    ) -> list[ValidationIssue]:
        """Static + optional data-aware validation. Empty list => OK."""
        ...

    def build_targets(
        self,
        spec: StrategySpec,
        context: StrategyExecutionContext,
    ) -> TargetPlan:
        """Compute the T x N target-weight plan for this spec + data context."""
        ...


__all__ = [
    "ValidationIssue",
    "StrategyExecutionContext",
    "TargetPlan",
    "StrategyExecutor",
]
