"""Canonical product-path error taxonomy (Phase 2.5 / 2.6).

Every error is a ``CanonicalError`` so the router can map it to a correct HTTP
status (422 for invalid request / semantic incompatibility, 400 for unexpected
client-side issues, 409 for idempotency conflict, 500 genuine server failure).
"""

from __future__ import annotations

from typing import Any


class CanonicalError(Exception):
    """Base class for all canonical-path errors (fail-closed)."""


class HashMismatchError(CanonicalError):
    """Strategy hash drift at any execution boundary."""


class UnregisteredExecutorError(CanonicalError):
    """Strategy type has no registered executor."""


class UnresolvedClauseError(CanonicalError):
    """Strategy has unresolved / unsupported clauses blocking execution."""


class DataUnavailableError(CanonicalError):
    """Requested data source could not be acquired (no silent fallback)."""


class PanelTooShortError(CanonicalError):
    """Supplied history is shorter than the executor's minimum requirements."""


class BoundedExecutionLimitError(CanonicalError):
    """Panel exceeds central bounded-execution limits (assets/bars/cells)."""


class IdempotencyConflictError(CanonicalError):
    """Same idempotency key was reused with a DIFFERENT request payload.

    Maps to HTTP 409 Conflict per Phase 2.6 section 3.2.
    """


class InvalidScenarioMechanismError(CanonicalError):
    """An unknown or invalid synthetic-stress mechanism was requested."""


class BaselineMismatchError(CanonicalError):
    """A supplied baseline_run_id does not belong to the same project/version/hash."""


class BenchmarkConflictError(CanonicalError):
    """Benchmark is in the tradable universe but benchmark_tradable is False."""


class ArtifactIntegrityError(CanonicalError):
    """A stored artifact is missing or fails hash/size verification."""


class ResourceNotFoundError(CanonicalError):
    """A requested durable resource (run/campaign/failure) does not exist."""


__all__ = [
    "CanonicalError",
    "HashMismatchError",
    "UnregisteredExecutorError",
    "UnresolvedClauseError",
    "DataUnavailableError",
    "PanelTooShortError",
    "BoundedExecutionLimitError",
    "IdempotencyConflictError",
    "InvalidScenarioMechanismError",
    "BaselineMismatchError",
    "BenchmarkConflictError",
    "ArtifactIntegrityError",
    "ResourceNotFoundError",
]


def _unused(*_args: Any) -> None:  # keep import linters calm if unused
    pass
