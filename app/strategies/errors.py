"""Strategy executor errors."""

from __future__ import annotations


class StrategyError(Exception):
    """Base class for strategy execution/registry errors."""


class UnknownStrategyType(StrategyError):
    """Requested a strategy type with no registered executor."""


class DuplicateExecutor(StrategyError):
    """Two executors registered for the same strategy type."""


class RegistryIncomplete(StrategyError):
    """A template/compiler/API advertises a type with no registered executor."""


class ExposureInfeasible(StrategyError):
    """Requested exposure cannot be met by the eligible universe."""


__all__ = [
    "StrategyError",
    "UnknownStrategyType",
    "DuplicateExecutor",
    "RegistryIncomplete",
    "ExposureInfeasible",
]
