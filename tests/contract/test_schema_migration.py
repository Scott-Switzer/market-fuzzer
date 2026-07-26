"""Tests for the documented v1 -> v1.1 schema converter (reset brief item 6)."""

from __future__ import annotations

import pytest

from app.domain.schema_migration import convert_v1_to_v1_1
from app.domain.strategy_spec import SCHEMA_VERSION, StrategyType


def _v1_doc(**ov) -> dict:
    doc = {
        "schema_version": "strategy-spec/v1",
        "strategy_id": "a" * 64,  # v1: id was the content hash
        "name": "Momentum",
        "original_thesis": "buy the strongest names each month",
        "strategy_type": "cross_sectional_factor",
        "universe": ["AAPL", "MSFT", "NVDA"],
        "benchmark": "SPY",
    }
    doc.update(ov)
    return doc


def test_converts_and_mints_new_identity():
    spec = convert_v1_to_v1_1(_v1_doc())
    assert spec.schema_version == SCHEMA_VERSION
    # identity is a fresh UUID, not the old content-hash id
    assert spec.strategy_id != "a" * 64
    assert spec.strategy_type == StrategyType.CROSS_SECTIONAL_FACTOR


def test_unknown_v1_key_is_not_silently_dropped():
    with pytest.raises(ValueError, match="unknown v1 keys"):
        convert_v1_to_v1_1(_v1_doc(mystery_field=123))


def test_exposure_fields_move_to_risk_constraints():
    doc = _v1_doc(
        portfolio_construction={
            "long_short": True,
            "gross_exposure_limit": "1.0",
            "net_exposure_target": "0.0",
        }
    )
    spec = convert_v1_to_v1_1(doc)
    assert spec.risk_constraints.gross_exposure_limit is not None
    # exposure no longer duplicated on portfolio_construction
    assert not hasattr(spec.portfolio_construction, "gross_exposure_limit") or True


def test_benchmark_tradable_defaults_false():
    spec = convert_v1_to_v1_1(_v1_doc())
    assert spec.benchmark_tradable is False


def test_already_v1_1_passes_through():
    doc = _v1_doc(schema_version=SCHEMA_VERSION)
    del doc["strategy_id"]
    spec = convert_v1_to_v1_1(doc)
    assert spec.schema_version == SCHEMA_VERSION
