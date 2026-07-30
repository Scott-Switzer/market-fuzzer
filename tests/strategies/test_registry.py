"""Registry / executor-support-gate tests (reset brief Phase 1.1 item 7)."""

from __future__ import annotations

import pytest

from app.domain.strategy_spec import StrategySpec, StrategyType
from app.strategies.contracts import TargetPlan, ValidationIssue
from app.strategies.errors import DuplicateExecutor, RegistryIncomplete, UnknownStrategyType
from app.strategies.registry import StrategyRegistry


class _FakeExecutor:
    def __init__(self, st: StrategyType) -> None:
        self.strategy_type = st

    def validate_spec(self, spec, context=None) -> list[ValidationIssue]:  # noqa: ANN001
        return []

    def build_targets(self, spec, context) -> TargetPlan:  # noqa: ANN001 - not used here
        raise NotImplementedError


def _spec(st: StrategyType = StrategyType.STATIC_ALLOCATION) -> StrategySpec:
    kw: dict[str, object] = {
        "name": "S",
        "original_thesis": "hold sixty forty and rebalance monthly",
        "strategy_type": st,
        "universe": ["VOO", "BND"],
        "benchmark": "SPY",
    }
    if st == StrategyType.STATIC_ALLOCATION:
        from app.domain.strategy_spec import PortfolioConstruction, Weighting

        kw["portfolio_construction"] = PortfolioConstruction(
            weighting=Weighting.FIXED, target_weights={"VOO": "0.6", "BND": "0.4"}
        )
    return StrategySpec(**kw)  # type: ignore[arg-type]


def test_register_and_get():
    reg = StrategyRegistry()
    ex = _FakeExecutor(StrategyType.STATIC_ALLOCATION)
    reg.register(ex)
    assert reg.get(StrategyType.STATIC_ALLOCATION) is ex
    assert reg.has(StrategyType.STATIC_ALLOCATION)
    assert reg.supported_types() == {StrategyType.STATIC_ALLOCATION}


def test_duplicate_registration_rejected():
    reg = StrategyRegistry()
    reg.register(_FakeExecutor(StrategyType.STATIC_ALLOCATION))
    with pytest.raises(DuplicateExecutor):
        reg.register(_FakeExecutor(StrategyType.STATIC_ALLOCATION))


def test_get_unknown_type_raises():
    reg = StrategyRegistry()
    with pytest.raises(UnknownStrategyType):
        reg.get(StrategyType.TACTICAL_ALLOCATION)


def test_validate_complete_fails_when_type_unbacked():
    reg = StrategyRegistry()
    reg.register(_FakeExecutor(StrategyType.STATIC_ALLOCATION))
    # The full enum is advertised but only one executor exists.
    with pytest.raises(RegistryIncomplete):
        reg.validate_complete()


def test_validate_complete_passes_for_backed_subset():
    reg = StrategyRegistry()
    reg.register(_FakeExecutor(StrategyType.STATIC_ALLOCATION))
    reg.validate_complete(advertised={StrategyType.STATIC_ALLOCATION})


def test_domain_gate_uses_registry_supported_types():
    """A spec whose type has no registered executor must be blocked by the
    domain approval gate -- 'supported' means 'implemented'."""
    reg = StrategyRegistry()  # empty: nothing supported
    spec = _spec(StrategyType.STATIC_ALLOCATION)
    reasons = spec.blocking_reasons(supported_types=reg.supported_types())
    assert any("no registered executor" in r for r in reasons)
    assert not spec.is_executable(supported_types=reg.supported_types())

    # Now register an executor for that type -> gate clears (for that reason).
    reg.register(_FakeExecutor(StrategyType.STATIC_ALLOCATION))
    reasons2 = spec.blocking_reasons(supported_types=reg.supported_types())
    assert not any("no registered executor" in r for r in reasons2)


def test_tactical_allocation_blocked_until_registered():
    reg = StrategyRegistry()
    reg.register(_FakeExecutor(StrategyType.STATIC_ALLOCATION))
    assert StrategyType.TACTICAL_ALLOCATION not in reg.supported_types()


def test_default_registry_backs_all_advertised_types():
    """After importing executors, every non-UNSUPPORTED enum member has an
    executor: UI/compiler/API 'supported' == registry 'implemented'."""
    import app.strategies.executors  # noqa: F401  registers all executors
    from app.strategies.registry import default_registry

    advertised = set(StrategyType) - {StrategyType.UNSUPPORTED}
    assert advertised == default_registry.supported_types()
    # validate_complete must not raise for the full advertised set
    default_registry.validate_complete()
