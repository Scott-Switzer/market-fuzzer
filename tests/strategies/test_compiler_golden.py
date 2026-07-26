"""Compiler golden tests (reset brief item 28).

Each supported example maps to the RIGHT strategy type with the right parameters,
and unsupported prose is NEVER coerced into a family.
"""

from __future__ import annotations

import pytest

from app.compiler import apply_resolutions, compile_thesis
from app.domain.strategy_spec import StrategyType

SUPPORTED_EXAMPLES = [
    (
        "Buy the top 5 stocks by 12-1 momentum each month.",
        StrategyType.LONG_ONLY_RANKING,
        {"long_count": 5, "frequency": "monthly"},
    ),
    (
        "Each month go long the top 20% and short the bottom 20% by 12-1 momentum.",
        StrategyType.CROSS_SECTIONAL_FACTOR,
        {"frequency": "monthly"},
    ),
    (
        "Buy SPY when its 20-day average is above its 50-day average, otherwise hold cash.",
        StrategyType.TIME_SERIES_SIGNAL,
        {"fast": 20, "slow": 50, "universe": ["SPY"]},
    ),
    (
        "Allocate 60% to SPY and 40% to AGG and rebalance monthly.",
        StrategyType.STATIC_ALLOCATION,
        {"universe": ["SPY", "AGG"], "frequency": "monthly"},
    ),
    (
        "Each month hold the top 3 asset ETFs by 12-month momentum if above their "
        "200-day average; otherwise hold BIL.",
        StrategyType.TACTICAL_ALLOCATION,
        {"top_k": 3, "trend": 200, "fallback": "BIL"},
    ),
]


@pytest.mark.parametrize("thesis,expected_type,checks", SUPPORTED_EXAMPLES)
def test_supported_example_classification(thesis, expected_type, checks):
    r = compile_thesis(thesis)
    spec = r.strategy_spec_draft
    assert spec.strategy_type == expected_type, f"{thesis!r} -> {spec.strategy_type}"
    assert not r.unsupported_clauses
    assert r.clauses  # never a single lump clause; multiple interpreted clauses
    assert len(r.clauses) >= 2

    if "long_count" in checks:
        assert spec.portfolio_construction.long_count == checks["long_count"]
    if "fast" in checks:
        sig = next(s for s in spec.signal_definitions if s.kind == "sma")
        assert sig.fast_window == checks["fast"] and sig.slow_window == checks["slow"]
    if "universe" in checks:
        assert spec.universe == checks["universe"]
    if "frequency" in checks:
        assert spec.frequency.value == checks["frequency"]
    if "top_k" in checks:
        assert spec.ranking_or_threshold_rules["top_k"] == checks["top_k"]
    if "fallback" in checks:
        assert spec.ranking_or_threshold_rules["fallback"] == checks["fallback"]


UNSUPPORTED_EXAMPLES = [
    "Make me rich with crypto vibes and good energy.",
    "Trade based on the CEO's astrology chart and lunar phases.",
    "Do whatever the news sentiment says using GPT and fundamentals.",
]


@pytest.mark.parametrize("thesis", UNSUPPORTED_EXAMPLES)
def test_unsupported_never_coerced(thesis):
    r = compile_thesis(thesis)
    assert r.strategy_spec_draft.strategy_type == StrategyType.UNSUPPORTED
    assert r.unsupported_clauses  # unsupported clauses present
    assert not r.is_supported


def test_long_only_momentum_not_mapped_to_long_short():
    # the specific forbidden behavior: long-only momentum must NOT become long/short
    r = compile_thesis("Buy the top 10 stocks by momentum each month.")
    spec = r.strategy_spec_draft
    assert spec.strategy_type == StrategyType.LONG_ONLY_RANKING
    assert spec.portfolio_construction.long_short is False
    assert not spec.portfolio_construction.short_count


def test_no_silent_sp500_universe():
    # missing universe must be a required resolution, NOT a hard-coded S&P 500
    r = compile_thesis("Buy the top 5 stocks by 12-1 momentum each month.")
    assert r.strategy_spec_draft.universe == []
    assert r.required_user_resolutions
    assert any("universe" in x.lower() for x in r.required_user_resolutions)


def test_clause_ledger_has_span_and_confidence():
    r = compile_thesis("Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    for c in r.clauses:
        assert c.original_text_span
        assert c.normalized_interpretation
        assert 0.0 <= c.confidence <= 1.0
        assert c.id in r.confidence_by_clause


def test_resolution_flow_makes_spec_executable():
    import app.strategies.executors  # noqa: F401 register
    from app.strategies.registry import default_registry

    r = compile_thesis("Each month go long the top 20% and short the bottom 20% by 12-1 momentum.")
    assert r.required_user_resolutions
    r2 = apply_resolutions(r, {"universe": ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]})
    assert not r2.required_user_resolutions
    assert r2.strategy_spec_draft.is_executable(supported_types=default_registry.supported_types())
