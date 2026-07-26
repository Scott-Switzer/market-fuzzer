"""Typed errors for the canonical product path (Phase 2.5 cutover)."""

from __future__ import annotations


class CanonicalError(Exception):
    """Base class for canonical-service errors."""


class UnsupportedStrategyError(CanonicalError):
    """The compiled strategy is unsupported and cannot be approved/run."""


class UnresolvedClauseError(CanonicalError):
    """A required resolution is still outstanding."""


class UnregisteredExecutorError(CanonicalError):
    """No registered executor backs the requested strategy type."""


class HashMismatchError(CanonicalError):
    """The requested canonical hash does not match the stored/computed hash."""


class DataUnavailableError(CanonicalError):
    """Requested market data could not be acquired (explicit; no silent fallback)."""


class BoundedExecutionLimitError(CanonicalError):
    """Request exceeds the synchronous bounded-execution limits."""


class PanelTooShortError(CanonicalError):
    """Supplied history is shorter than the executor requires for lookbacks."""


__all__ = [
    "CanonicalError",
    "UnsupportedStrategyError",
    "UnresolvedClauseError",
    "UnregisteredExecutorError",
    "HashMismatchError",
    "DataUnavailableError",
    "BoundedExecutionLimitError",
    "PanelTooShortError",
]
