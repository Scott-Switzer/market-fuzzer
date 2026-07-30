"""Concrete strategy executors + explicit registration into the default registry.

Importing this package registers all Phase 2 executors. Registration is explicit
(reset brief item 16): we construct each executor and call ``register`` -- no
entry-point scanning, no import-order-dependent decorators.
"""

from __future__ import annotations

from app.strategies.contracts import StrategyExecutor
from app.strategies.executors.cross_sectional_factor import CrossSectionalFactorExecutor
from app.strategies.executors.long_only_ranking import LongOnlyRankingExecutor
from app.strategies.executors.static_allocation import StaticAllocationExecutor
from app.strategies.executors.tactical_allocation import TacticalAllocationExecutor
from app.strategies.executors.time_series_signal import TimeSeriesSignalExecutor
from app.strategies.registry import StrategyRegistry, default_registry

ALL_EXECUTORS: list[StrategyExecutor] = [
    CrossSectionalFactorExecutor(),
    LongOnlyRankingExecutor(),
    TimeSeriesSignalExecutor(),
    StaticAllocationExecutor(),
    TacticalAllocationExecutor(),
]


def register_all(registry: StrategyRegistry) -> None:
    """Register every Phase 2 executor into ``registry`` (idempotent-safe: clears first)."""
    registry.clear()
    for ex in ALL_EXECUTORS:
        registry.register(ex)


# Register into the process-wide default registry on import.
register_all(default_registry)


__all__ = [
    "CrossSectionalFactorExecutor",
    "LongOnlyRankingExecutor",
    "TimeSeriesSignalExecutor",
    "StaticAllocationExecutor",
    "TacticalAllocationExecutor",
    "ALL_EXECUTORS",
    "register_all",
]
