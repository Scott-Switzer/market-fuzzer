"""Explicit strategy executor registry (reset brief Phase 1.1 item 7 / Phase 2 item 16).

The registry is the single source of truth for which strategy types are actually
executable. "Supported" means "has a registered executor" -- nothing else.

Design constraints (reset brief):
* No entry-point discovery, no import scanning, no import-order-dependent
  decorators. Registration is explicit and deterministic.
* ``StrategySpec`` (the domain contract) must NOT import this module. The
  dependency arrow points strategies -> domain, never the reverse. Callers pass
  ``registry.supported_types()`` into the domain approval gate.

In Phase 1.1 the registry exists and is wired into approval; concrete executors
are registered in Phase 2. ``validate_complete`` lets tests assert that every
advertised type (enum/template/compiler/API) is backed by a real executor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.domain.strategy_spec import StrategyType
from app.strategies.errors import DuplicateExecutor, RegistryIncomplete, UnknownStrategyType

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids runtime import cycle
    from app.strategies.contracts import StrategyExecutor


@runtime_checkable
class _ExecutorLike(Protocol):
    strategy_type: StrategyType


class StrategyRegistry:
    """Explicit map of ``StrategyType`` -> executor instance."""

    def __init__(self) -> None:
        self._executors: dict[StrategyType, StrategyExecutor] = {}

    def register(self, executor: StrategyExecutor) -> None:
        st = executor.strategy_type
        if st in self._executors:
            raise DuplicateExecutor(f"executor already registered for {st!r}")
        self._executors[st] = executor

    def get(self, strategy_type: StrategyType) -> StrategyExecutor:
        try:
            return self._executors[strategy_type]
        except KeyError as e:
            raise UnknownStrategyType(f"no registered executor for {strategy_type!r}") from e

    def has(self, strategy_type: StrategyType) -> bool:
        return strategy_type in self._executors

    def supported_types(self) -> set[StrategyType]:
        """The set of strategy types that are genuinely executable."""
        return set(self._executors)

    def validate_complete(self, advertised: set[StrategyType] | None = None) -> None:
        """Fail if any advertised type lacks a registered executor.

        ``advertised`` defaults to "every non-UNSUPPORTED enum member". Pass the
        template/compiler/API advertised set to assert they never promise a type
        the registry cannot execute.
        """
        if advertised is None:
            advertised = set(StrategyType) - {StrategyType.UNSUPPORTED}
        missing = advertised - self.supported_types()
        # UNSUPPORTED is never executable and never counts as missing.
        missing.discard(StrategyType.UNSUPPORTED)
        if missing:
            raise RegistryIncomplete(
                "advertised strategy types without a registered executor: "
                + ", ".join(sorted(m.value for m in missing))
            )

    def clear(self) -> None:
        """Test helper."""
        self._executors.clear()


# Process-wide default registry. Executors register into this in Phase 2 via
# app.strategies.executors (explicit registration, not import side effects on
# unrelated modules).
default_registry = StrategyRegistry()


def supported_types() -> set[StrategyType]:
    return default_registry.supported_types()


__all__ = [
    "StrategyRegistry",
    "default_registry",
    "supported_types",
]
