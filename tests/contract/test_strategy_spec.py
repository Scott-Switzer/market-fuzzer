"""Contract tests for the canonical StrategySpec (schema strategy-spec/v1.1).

These are foundational: every later stage relies on the hash invariant, the
identity-vs-content separation, and the execution-gating semantics proven here.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from app.domain.strategy_spec import (
    Clause,
    ClauseState,
    ExecutionTiming,
    Frequency,
    OrderPolicy,
    PortfolioConstruction,
    RiskConstraints,
    StrategySpec,
    StrategyType,
    Weighting,
)

SUPPORTED = set(StrategyType) - {StrategyType.UNSUPPORTED}


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
# canonical hash invariants
# ---------------------------------------------------------------------------
def test_hash_is_deterministic():
    a = _base_spec()
    b = _base_spec()
    assert a.canonical_hash == b.canonical_hash
    assert a.canonical_hash == a.compute_hash()
    assert len(a.canonical_hash) == 64  # sha256 hex


def test_strategy_id_is_not_content_hash():
    # Identity is a stable UUID, NOT derived from content.
    a = _base_spec()
    b = _base_spec()
    assert a.strategy_id != a.canonical_hash
    assert a.strategy_id != b.strategy_id  # two constructions => two identities
    # ...yet identical content => identical hash
    assert a.canonical_hash == b.canonical_hash


def test_canonical_hash_is_computed_property_no_drift():
    a = _base_spec()
    # There is no stored canonical_hash field to drift; it recomputes each time.
    assert "canonical_hash" not in a.model_dump()
    assert a.canonical_hash == a.compute_hash()


def test_volatile_metadata_does_not_change_hash():
    a = _base_spec()
    b = _base_spec(
        strategy_version=99,
        compiler_metadata={"trace_id": "abc", "llm": "gpt-x", "ts": 12345},
    )
    assert a.canonical_hash == b.canonical_hash


def test_changing_an_executable_param_changes_hash():
    a = _base_spec()
    assert a.canonical_hash != _base_spec(frequency=Frequency.WEEKLY).canonical_hash
    assert a.canonical_hash != _base_spec(universe=["AAPL", "MSFT", "NVDA"]).canonical_hash
    d = _base_spec(
        portfolio_construction=PortfolioConstruction(long_short=True, weighting=Weighting.INVERSE_VOLATILITY)
    )
    assert a.canonical_hash != d.canonical_hash


def test_universe_order_matters_for_hash():
    a = _base_spec(universe=["AAPL", "MSFT"])
    b = _base_spec(universe=["MSFT", "AAPL"])
    assert a.canonical_hash != b.canonical_hash


def test_canonical_dict_excludes_all_volatile_keys():
    a = _base_spec(compiler_metadata={"x": 1}, strategy_version=7)
    d = a.canonical_dict()
    for k in ("strategy_id", "strategy_version", "compiler_metadata"):
        assert k not in d


def test_roundtrip_json_preserves_hash():
    a = _base_spec()
    restored = StrategySpec.model_validate_json(a.model_dump_json())
    assert restored.canonical_hash == a.canonical_hash


# ---------------------------------------------------------------------------
# Decimal normalization
# ---------------------------------------------------------------------------
def test_decimal_weights_normalize_for_hash():
    a = _base_spec(
        strategy_type=StrategyType.STATIC_ALLOCATION,
        universe=["VOO", "BND"],
        benchmark=None,
        portfolio_construction=PortfolioConstruction(
            weighting=Weighting.FIXED, target_weights={"VOO": "0.60", "BND": "0.40"}
        ),
    )
    b = _base_spec(
        strategy_type=StrategyType.STATIC_ALLOCATION,
        universe=["VOO", "BND"],
        benchmark=None,
        portfolio_construction=PortfolioConstruction(
            weighting=Weighting.FIXED, target_weights={"VOO": 0.6, "BND": 0.4}
        ),
    )
    assert a.canonical_hash == b.canonical_hash


def test_nan_and_inf_weights_rejected():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            PortfolioConstruction(target_weights={"VOO": bad})


def test_negative_static_weight_rejected():
    with pytest.raises(ValidationError):
        PortfolioConstruction(target_weights={"VOO": "-0.1"})


# ---------------------------------------------------------------------------
# universe normalization
# ---------------------------------------------------------------------------
def test_universe_is_normalized_and_deduped():
    a = _base_spec(universe=[" aapl ", "MSFT"])
    assert a.universe == ["AAPL", "MSFT"]


def test_duplicate_ticker_rejected():
    with pytest.raises(ValidationError):
        _base_spec(universe=["AAPL", "aapl"])


def test_blank_ticker_rejected():
    with pytest.raises(ValidationError):
        _base_spec(universe=["AAPL", "   "])


def test_legit_dash_symbol_preserved():
    a = _base_spec(universe=["BRK-B", "AAPL"])
    assert "BRK-B" in a.universe


def test_thesis_min_length_enforced():
    with pytest.raises(ValidationError):
        _base_spec(original_thesis="short")


# ---------------------------------------------------------------------------
# clause preservation
# ---------------------------------------------------------------------------
def test_unsupported_clause_is_preserved_not_dropped():
    unsupported = Clause(
        id="c_options", original_text="hedge with 3-month puts", state=ClauseState.UNSUPPORTED
    )
    a = _base_spec(unsupported_clauses=[unsupported])
    assert len(a.unsupported_clauses) == 1
    assert a.unsupported_clauses[0].id == "c_options"


# ---------------------------------------------------------------------------
# execution gating
# ---------------------------------------------------------------------------
def test_clean_spec_is_executable():
    a = _base_spec()
    assert a.is_executable(supported_types=SUPPORTED)
    assert a.blocking_reasons(supported_types=SUPPORTED) == []


def test_type_without_registered_executor_blocks():
    a = _base_spec(strategy_type=StrategyType.CROSS_SECTIONAL_FACTOR)
    reasons = a.blocking_reasons(supported_types={StrategyType.STATIC_ALLOCATION})
    assert any("no registered executor" in r for r in reasons)


def test_unresolved_clause_blocks_execution():
    a = _base_spec(clauses=[Clause(id="c1", original_text="something", state=ClauseState.UNRESOLVED)])
    reasons = a.blocking_reasons(supported_types=SUPPORTED)
    assert any("unresolved" in r for r in reasons)
    assert not a.is_executable(supported_types=SUPPORTED)


def test_rejected_clause_blocks_execution():
    a = _base_spec(clauses=[Clause(id="c1", original_text="rm -rf", state=ClauseState.REJECTED)])
    assert any("rejected" in r for r in a.blocking_reasons(supported_types=SUPPORTED))


def test_unsupported_strategy_type_blocks():
    a = _base_spec(strategy_type=StrategyType.UNSUPPORTED)
    assert any("unsupported" in r for r in a.blocking_reasons(supported_types=SUPPORTED))


def test_empty_universe_blocks():
    a = _base_spec(universe=[])
    assert any("universe" in r for r in a.blocking_reasons(supported_types=SUPPORTED))


def test_missing_data_blocks():
    a = _base_spec(universe=["AAPL", "ZZZZ"])
    reasons = a.blocking_reasons(available_symbols={"AAPL", "MSFT"}, supported_types=SUPPORTED)
    assert any("ZZZZ" in r for r in reasons)


def test_benchmark_in_universe_blocks_unless_tradable():
    a = _base_spec(universe=["AAPL", "SPY"], benchmark="SPY")
    assert any("benchmark" in r.lower() for r in a.blocking_reasons(supported_types=SUPPORTED))
    # explicitly tradable => allowed
    b = _base_spec(universe=["AAPL", "SPY"], benchmark="SPY", benchmark_tradable=True)
    assert not any("benchmark" in r.lower() for r in b.blocking_reasons(supported_types=SUPPORTED))


def test_net_exceeds_gross_blocks():
    a = _base_spec(risk_constraints=RiskConstraints(gross_exposure_limit="1.0", net_exposure_target="2.0"))
    assert any("net exposure" in r.lower() for r in a.blocking_reasons(supported_types=SUPPORTED))


def test_same_close_without_explicit_allow_blocks():
    a = _base_spec(execution_timing=ExecutionTiming.SAME_CLOSE)
    assert any("same-bar" in r or "look-ahead" in r for r in a.blocking_reasons(supported_types=SUPPORTED))


def test_same_close_present_false_key_still_blocks():
    # A present-but-false allow flag must NOT pass.
    a = _base_spec(
        execution_timing=ExecutionTiming.SAME_CLOSE,
        order_policy=OrderPolicy(allow_same_bar=False),
    )
    assert any("same-bar" in r or "look-ahead" in r for r in a.blocking_reasons(supported_types=SUPPORTED))


def test_same_close_with_explicit_allow_ok():
    a = _base_spec(execution_timing=ExecutionTiming.SAME_CLOSE, order_policy=OrderPolicy(allow_same_bar=True))
    assert not any(
        "same-bar" in r or "look-ahead" in r for r in a.blocking_reasons(supported_types=SUPPORTED)
    )


def test_arbitrary_code_blocks():
    a = _base_spec(order_policy=OrderPolicy(arbitrary_code=True))
    assert any("arbitrary code" in r.lower() for r in a.blocking_reasons(supported_types=SUPPORTED))


def test_static_allocation_requires_weights():
    a = _base_spec(strategy_type=StrategyType.STATIC_ALLOCATION, universe=["VOO", "BND"], benchmark=None)
    assert any("target_weights" in r for r in a.blocking_reasons(supported_types=SUPPORTED))


def test_static_allocation_weights_must_sum_to_one():
    a = _base_spec(
        strategy_type=StrategyType.STATIC_ALLOCATION,
        universe=["VOO", "BND"],
        benchmark=None,
        portfolio_construction=PortfolioConstruction(target_weights={"VOO": "0.6", "BND": "0.3"}),
    )
    assert any("sum to" in r for r in a.blocking_reasons(supported_types=SUPPORTED))


def test_static_allocation_weight_outside_universe_blocks():
    a = _base_spec(
        strategy_type=StrategyType.STATIC_ALLOCATION,
        universe=["VOO", "BND"],
        benchmark=None,
        portfolio_construction=PortfolioConstruction(target_weights={"VOO": "0.6", "QQQ": "0.4"}),
    )
    assert any("outside the universe" in r for r in a.blocking_reasons(supported_types=SUPPORTED))


def test_valid_static_allocation_is_executable():
    a = _base_spec(
        strategy_type=StrategyType.STATIC_ALLOCATION,
        universe=["VOO", "BND"],
        benchmark="SPY",
        portfolio_construction=PortfolioConstruction(
            weighting=Weighting.FIXED, target_weights={"VOO": "0.6", "BND": "0.4"}
        ),
    )
    assert a.is_executable(supported_types=SUPPORTED), a.blocking_reasons(supported_types=SUPPORTED)


# ---------------------------------------------------------------------------
# property-based
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


def test_top_level_mutation_is_frozen():
    a = _base_spec()
    with pytest.raises(ValidationError):
        a.name = "changed"  # type: ignore[misc]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
