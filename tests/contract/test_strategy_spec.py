"""Contract tests for the canonical StrategySpec.

These are foundational: every later stage relies on the hash invariant and the
execution-gating semantics proven here.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.strategy_spec import (
    Clause,
    ClauseState,
    ExecutionTiming,
    Frequency,
    PortfolioConstruction,
    StrategySpec,
    StrategyType,
    Weighting,
)


def _base_spec(**overrides: object) -> StrategySpec:
    kwargs: dict[str, object] = {
        "name": "Test Momentum",
        "original_thesis": "Buy the strongest recent winners each month.",
        "strategy_type": StrategyType.CROSS_SECTIONAL_FACTOR,
        "universe": ["AAPL", "MSFT", "NVDA", "AMZN"],
        "benchmark": "SPY",
        "frequency": Frequency.MONTHLY,
    }
    kwargs.update(overrides)
    return StrategySpec(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# canonical hash invariants (release gate section 16-19)
# ---------------------------------------------------------------------------
def test_hash_is_deterministic():
    a = _base_spec()
    b = _base_spec()
    assert a.canonical_hash == b.canonical_hash
    assert a.canonical_hash == a.compute_hash()
    assert len(a.canonical_hash) == 64  # sha256 hex


def test_strategy_id_defaults_to_hash():
    a = _base_spec()
    assert a.strategy_id == a.canonical_hash


def test_volatile_metadata_does_not_change_hash():
    a = _base_spec()
    b = _base_spec(
        strategy_version=99,
        compiler_metadata={"trace_id": "abc", "llm": "gpt-x", "ts": 12345},
    )
    assert a.canonical_hash == b.canonical_hash, "volatile metadata must not affect the canonical hash"


def test_changing_an_executable_param_changes_hash():
    a = _base_spec()
    b = _base_spec(frequency=Frequency.WEEKLY)
    assert a.canonical_hash != b.canonical_hash

    c = _base_spec(universe=["AAPL", "MSFT", "NVDA"])  # different universe
    assert a.canonical_hash != c.canonical_hash

    d = _base_spec(
        portfolio_construction=PortfolioConstruction(long_short=True, weighting=Weighting.INVERSE_VOLATILITY)
    )
    assert a.canonical_hash != d.canonical_hash


def test_universe_order_matters_for_hash():
    # Order is meaningful (index alignment), so a reorder is a different spec.
    a = _base_spec(universe=["AAPL", "MSFT"])
    b = _base_spec(universe=["MSFT", "AAPL"])
    assert a.canonical_hash != b.canonical_hash


def test_canonical_dict_excludes_all_volatile_keys():
    a = _base_spec(compiler_metadata={"x": 1}, strategy_version=7)
    d = a.canonical_dict()
    for k in ("strategy_id", "strategy_version", "compiler_metadata", "canonical_hash"):
        assert k not in d


def test_roundtrip_json_preserves_hash():
    a = _base_spec()
    restored = StrategySpec.model_validate_json(a.model_dump_json())
    assert restored.canonical_hash == a.canonical_hash


# ---------------------------------------------------------------------------
# clause preservation (never silently drop)
# ---------------------------------------------------------------------------
def test_unsupported_clause_is_preserved_not_dropped():
    unsupported = Clause(
        id="c_options", original_text="hedge with 3-month puts", state=ClauseState.UNSUPPORTED
    )
    a = _base_spec(unsupported_clauses=[unsupported])
    assert len(a.unsupported_clauses) == 1
    assert a.unsupported_clauses[0].id == "c_options"


# ---------------------------------------------------------------------------
# execution gating (reset brief section 5)
# ---------------------------------------------------------------------------
def test_clean_spec_is_executable():
    a = _base_spec()
    assert a.is_executable()
    assert a.blocking_reasons() == []


def test_unresolved_clause_blocks_execution():
    a = _base_spec(clauses=[Clause(id="c1", original_text="something", state=ClauseState.UNRESOLVED)])
    reasons = a.blocking_reasons()
    assert any("unresolved" in r for r in reasons)
    assert not a.is_executable()


def test_rejected_clause_blocks_execution():
    a = _base_spec(clauses=[Clause(id="c1", original_text="rm -rf", state=ClauseState.REJECTED)])
    assert any("rejected" in r for r in a.blocking_reasons())


def test_unsupported_strategy_type_blocks():
    a = _base_spec(strategy_type=StrategyType.UNSUPPORTED)
    assert any("unsupported" in r for r in a.blocking_reasons())


def test_empty_universe_blocks():
    a = _base_spec(universe=[])
    assert any("universe" in r for r in a.blocking_reasons())


def test_missing_data_blocks():
    a = _base_spec(universe=["AAPL", "ZZZZ"])
    reasons = a.blocking_reasons(available_symbols={"AAPL", "MSFT"})
    assert any("ZZZZ" in r for r in reasons)


def test_benchmark_in_universe_blocks():
    a = _base_spec(universe=["AAPL", "SPY"], benchmark="SPY")
    assert any("benchmark" in r.lower() for r in a.blocking_reasons())


def test_net_exceeds_gross_blocks():
    a = _base_spec(
        portfolio_construction=PortfolioConstruction(gross_exposure_limit=1.0, net_exposure_limit=2.0)
    )
    assert any("net exposure" in r.lower() for r in a.blocking_reasons())


def test_same_close_without_explicit_allow_blocks():
    a = _base_spec(execution_timing=ExecutionTiming.SAME_CLOSE)
    assert any("same-bar" in r or "look-ahead" in r for r in a.blocking_reasons())


def test_same_close_with_explicit_allow_ok():
    a = _base_spec(execution_timing=ExecutionTiming.SAME_CLOSE, order_policy={"allow_same_bar": True})
    assert not any("same-bar" in r or "look-ahead" in r for r in a.blocking_reasons())


def test_arbitrary_code_blocks():
    a = _base_spec(order_policy={"arbitrary_code": "import os; os.system('x')"})
    assert any("arbitrary code" in r.lower() for r in a.blocking_reasons())


def test_static_allocation_requires_weights():
    a = _base_spec(strategy_type=StrategyType.STATIC_ALLOCATION, universe=["VOO", "BND"])
    assert any("target_weights" in r for r in a.blocking_reasons())


def test_static_allocation_weights_must_sum_to_one():
    a = _base_spec(
        strategy_type=StrategyType.STATIC_ALLOCATION,
        universe=["VOO", "BND"],
        portfolio_construction=PortfolioConstruction(target_weights={"VOO": 0.6, "BND": 0.3}),
    )
    assert any("sum to" in r for r in a.blocking_reasons())


def test_valid_static_allocation_is_executable():
    a = _base_spec(
        strategy_type=StrategyType.STATIC_ALLOCATION,
        universe=["VOO", "BND"],
        benchmark="SPY",
        portfolio_construction=PortfolioConstruction(
            weighting=Weighting.FIXED, target_weights={"VOO": 0.6, "BND": 0.4}
        ),
    )
    assert a.is_executable(), a.blocking_reasons()


# ---------------------------------------------------------------------------
# property-based: hash stability under volatile churn
# ---------------------------------------------------------------------------
@given(
    version=st.integers(min_value=1, max_value=10_000),
    meta=st.dictionaries(st.text(min_size=1, max_size=8), st.integers(), max_size=5),
)
def test_property_volatile_churn_never_changes_hash(version, meta):
    base = _base_spec()
    churned = _base_spec(strategy_version=version, compiler_metadata=meta)
    assert base.canonical_hash == churned.canonical_hash


@given(freq=st.sampled_from(list(Frequency)))
def test_property_frequency_is_in_hash_domain(freq):
    a = _base_spec(frequency=Frequency.MONTHLY)
    b = _base_spec(frequency=freq)
    if freq == Frequency.MONTHLY:
        assert a.canonical_hash == b.canonical_hash
    else:
        assert a.canonical_hash != b.canonical_hash


def test_deepcopy_preserves_hash():
    a = _base_spec()
    b = copy.deepcopy(a)
    assert a.canonical_hash == b.canonical_hash


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
