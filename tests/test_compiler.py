import pytest

from app.compiler import compile_offline


def test_offline_compiler_supports_all_demo_controls():
    result = compile_offline(
        "Thin liquidity, negative earnings shock, crowded momentum, forced seller, high latency, elevated volatility",
        77,
    )
    assert result.spec.seed == 77
    assert result.spec.exchange.baseline_depth == 260
    assert result.spec.exchange.latency_profile == "high"
    assert result.spec.macro.volatility_regime == "elevated"
    assert {event.type for event in result.spec.events} >= {"earnings", "liquidity_withdrawal"}
    assert result.spec_hash == result.spec.specification_hash()


def test_offline_compiler_rejects_contradiction_and_size_limit():
    with pytest.raises(ValueError, match="contradictory"):
        compile_offline("Create both deep liquidity and thin liquidity")
    with pytest.raises(ValueError, match="2,000"):
        compile_offline("x" * 2001)
