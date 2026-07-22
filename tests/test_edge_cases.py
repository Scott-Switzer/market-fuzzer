from __future__ import annotations

import numpy as np
import pytest

from app.break_test.service import run_break_test
from app.break_test.strategy_compiler import StrategyCompiler, classify_strategy


def _prices() -> list[float]:
    return (100.0 + np.arange(260) * 0.1).tolist()


class TestCustomPythonStrategies:
    def test_missing_strategy_function_raises(self) -> None:
        with pytest.raises(Exception):
            run_break_test(_prices(), strategy_type="python", strategy_code="x=1", worlds_per_regime=10)

    def test_valid_python_strategy_runs(self) -> None:
        code = """
def strategy(observations, params):
    return [{'action_type': 'hold'} for _ in observations]
"""
        result = run_break_test(_prices(), strategy_type="python", strategy_code=code, worlds_per_regime=10)
        assert "historical" in result
        assert "forward_test" in result

    def test_python_strategy_with_invalid_action_raises(self) -> None:
        code = """
def strategy(observations, params):
    return [{'action_type': 'unknown'} for _ in observations]
"""
        with pytest.raises(Exception):
            run_break_test(_prices(), strategy_type="python", strategy_code=code, worlds_per_regime=10)

    def test_python_strategy_forward_mode_exchange(self) -> None:
        code = """
def strategy(observations, params):
    return [{'action_type': 'hold'} for _ in observations]
"""
        result = run_break_test(
            _prices(),
            strategy_type="python",
            strategy_code=code,
            worlds_per_regime=10,
            forward_mode="exchange",
        )
        assert result["forward_mode"] == "exchange"
        assert "forward_test" in result

class TestPlainEnglishCompilerRouting:
    def test_pairs_relative_value_routes_before_trending(self) -> None:
        out = classify_strategy("pairs trade this spread with relative value and cointegration")
        assert out["template_key"] == "pairs_relative_value"
        assert out["confidence"] == "high"
        assert out["match_method"] == "ordered_cluster"

    def test_volatility_compression_routes_before_trending(self) -> None:
        out = classify_strategy("volatility compression and low volatility after a tight range")
        assert out["template_key"] == "volatility_compression"
        assert out["confidence"] == "high"

    def test_trending_momentum_fallback(self) -> None:
        out = classify_strategy("trend and momentum with moving average follow")
        assert out["template_key"] == "trending_momentum"
        assert out["confidence"] == "high"

    def test_mean_reversion_routes_before_trending(self) -> None:
        out = classify_strategy("oversold mean reversion bounce expecting a return to normal")
        assert out["template_key"] == "mean_reversion"
        assert out["confidence"] == "high"

    def test_breakout_follow_routes_before_trending(self) -> None:
        out = classify_strategy("breakout above the recent high with fixed hold")
        assert out["template_key"] == "breakout_follow"
        assert out["confidence"] == "high"

    def test_unknown_text_falls_back_to_trending_momentum(self) -> None:
        out = classify_strategy("totally unrelated nonsense with no trade idea")
        assert out["template_key"] == "trending_momentum"
        assert out["confidence"] == "low"
        assert out["match_method"] == "default_fallback"


class TestPlainEnglishCompilerMeta:
    def test_available_templates_exposes_meta_fields(self) -> None:
        from app.break_test.strategy_compiler import available_templates

        templates = available_templates()
        assert set(templates) == {
            "trending_momentum",
            "mean_reversion",
            "breakout_follow",
            "volatility_compression",
            "pairs_relative_value",
        }
        pairs_meta = templates["pairs_relative_value"]
        assert pairs_meta["meta_key"] == "pairs_relative_value"
        assert any(item["name"] == "symbol_b_prices" for item in pairs_meta["inputs"])
        assert pairs_meta["expected_observations"]

    def test_template_code_output_has_inputs_outputs_observations(self) -> None:
        out = classify_strategy("pairs trade with cointegration")
        template_code = out["template_code"]
        assert template_code["template_key"] == "pairs_relative_value"
        assert template_code["meta_key"] == "pairs_relative_value"
        assert template_code["inputs"]
        assert template_code["outputs"]
        assert template_code["expected_observations"]

    def test_classify_output_contains_code_and_defaults(self) -> None:
        out = classify_strategy("Buy when price crosses above its moving average")
        assert out["template_key"] == "trending_momentum"
        assert out["code"].startswith("def strategy(observations, params):")
        assert out["defaults"] == {"fast": 20, "slow": 50}


class TestPlainEnglishCompilerExecution:
    def test_trending_momentum_classification(self) -> None:
        out = classify_strategy("Buy when price crosses above its moving average and exit when it falls below")
        assert out["template_key"] == "trending_momentum"
        assert "code" in out

    def test_mean_reversion_classification(self) -> None:
        out = classify_strategy("Buy after drops and sell after rises, expecting a return to normal")
        assert out["template_key"] == "mean_reversion"

    def test_breakout_classification(self) -> None:
        out = classify_strategy("Buy when price breaks above the recent high")
        assert out["template_key"] == "breakout_follow"

    def test_plain_english_api_runs(self) -> None:
        result = run_break_test(
            _prices(),
            strategy_type="plain_english",
            plain_english="Buy when price crosses above its moving average and exit when it falls below",
            params={},
            worlds_per_regime=10,
        )
        assert "historical" in result
        assert "forward_test" in result

    def test_plain_english_classification_outputs_auditable_template_code(self) -> None:
        out = classify_strategy("Buy when price crosses above its moving average and exit when it falls below")
        assert out["template_key"] == "trending_momentum"
        assert out["match_method"] == "ordered_cluster"
        template_code = out["template_code"]
        assert template_code["meta_key"] == "trending_momentum"
        assert template_code["inputs"]
        assert template_code["outputs"]
        assert template_code["expected_observations"]
