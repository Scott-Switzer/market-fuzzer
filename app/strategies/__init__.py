"""Fenrix strategy execution package.

Public surface:
* ``StrategyRegistry`` / ``default_registry`` -- explicit executor registry.
* ``StrategyExecutor`` / ``TargetPlan`` / ``StrategyExecutionContext`` -- the
  executor contract (Phase 2 item 15).

Concrete executors live in ``app.strategies.executors`` and register themselves
explicitly (Phase 2); Phase 1.1 ships the registry + contracts foundation.
"""

from __future__ import annotations

from app.strategies.accounting import BacktestResult, CostModel, run_accounting
from app.strategies.contracts import (
    StrategyExecutionContext,
    StrategyExecutor,
    TargetPlan,
    ValidationIssue,
)
from app.strategies.errors import (
    DuplicateExecutor,
    ExposureInfeasible,
    RegistryIncomplete,
    StrategyError,
    UnknownStrategyType,
)
from app.strategies.pipeline import run_strategy
from app.strategies.registry import StrategyRegistry, default_registry, supported_types

__all__ = [
    "StrategyExecutionContext",
    "StrategyExecutor",
    "TargetPlan",
    "ValidationIssue",
    "StrategyError",
    "UnknownStrategyType",
    "DuplicateExecutor",
    "RegistryIncomplete",
    "ExposureInfeasible",
    "StrategyRegistry",
    "default_registry",
    "supported_types",
    "BacktestResult",
    "CostModel",
    "run_accounting",
    "run_strategy",
]
