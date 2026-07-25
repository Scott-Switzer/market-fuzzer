from __future__ import annotations

from typing import Any


class DeterministicFallbackCompiler:
    _TEMPLATES = {
        "sma_crossover": (
            "Buy when the {fast}-period moving average crosses above the {slow}-period moving average, "
            "and sell when it crosses back below."
        ),
        "rsi_reversion": (
            "Buy when RSI({lookback}) drops below {oversold} and sell when it rises above {overbought}."
        ),
        "breakout": ("Buy when price reaches a new {lookback}-period high, then exit after {hold} bars."),
        "long_only_momentum": (
            "Go long the asset with the highest {lookback}-day momentum and equal-weight the {n}-asset portfolio."
        ),
        "beta_neutral_factor": (
            "Dollar-neutral portfolio targeting zero market beta using {lookback}-day rolling beta."
        ),
        "macro_gated_risk_off": (
            "Hold equity only when {macro_series} is above its {lookback}-day moving average; otherwise hold cash."
        ),
    }

    @classmethod
    def available_examples(cls) -> dict[str, str]:
        return dict(cls._TEMPLATES)

    @classmethod
    def classify(cls, text: str) -> dict[str, Any]:
        lowered = text.lower()
        defaults: dict[str, Any]
        if "sma" in lowered or "moving average" in lowered or "crossover" in lowered:
            template_key = "sma_crossover"
            defaults = {"fast": 20, "slow": 50}
        elif "rsi" in lowered or "oversold" in lowered or "overbought" in lowered:
            template_key = "rsi_reversion"
            defaults = {"lookback": 14, "oversold": 30.0, "overbought": 70.0}
        elif "breakout" in lowered or "new high" in lowered:
            template_key = "breakout"
            defaults = {"lookback": 20, "hold": 10}
        elif "long-only momentum" in lowered or "equal-weight" in lowered and "momentum" in lowered:
            template_key = "long_only_momentum"
            defaults = {"lookback": 60, "n": 10}
        elif "beta-neutral" in lowered or "dollar-neutral" in lowered:
            template_key = "beta_neutral_factor"
            defaults = {"lookback": 60}
        elif "risk-off" in lowered or "cash" in lowered or "moving average" in lowered:
            template_key = "macro_gated_risk_off"
            defaults = {"lookback": 200, "macro_series": "SPY"}
        else:
            template_key = "sma_crossover"
            defaults = {"fast": 20, "slow": 50}
        return {
            "template_key": template_key,
            "defaults": defaults,
            "confidence": "high",
            "compiler": "deterministic_fallback",
        }
