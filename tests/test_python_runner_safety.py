from __future__ import annotations

import numpy as np
import pytest

from app.break_test.python_runner import (
    _assert_no_unsafe_imports,
    run_python_strategy_with_np,
)


def _build_observations() -> list[dict]:
    prices = (100.0 + np.arange(60) * 0.1).tolist()
    return [
        {
            "step": i,
            "symbol": "ASSET",
            "side": "buy",
            "mid_ticks": int(px),
            "best_bid_ticks": int(px * 0.999),
            "best_ask_ticks": int(px * 1.001),
            "spread_bps": 2.0,
            "observed_volume": 100_000,
            "inventory": 0,
            "remaining_quantity": 0,
            "exchange_latency_profile": "normal",
            "intervention_active": False,
        }
        for i, px in enumerate(prices)
    ]


class TestPythonRunnerSafety:
    def test_allows_numpy_alias_np(self) -> None:
        code = """
import numpy as np

def strategy(observations, params):
    prices = np.array([obs.get("mid_ticks", 100) for obs in observations], dtype=float)
    return [{"action_type": "hold"} for _ in observations]
"""
        result = run_python_strategy_with_np(code, _build_observations(), {"fast": 10, "slow": 20})
        assert isinstance(result, list)
        assert all(action["action_type"] == "hold" for action in result)

    def test_rejects_os_import(self) -> None:
        code = """
import os

def strategy(observations, params):
    return [{"action_type": "hold"} for _ in observations]
"""
        with pytest.raises(ValueError, match="disallowed imports"):
            run_python_strategy_with_np(code, _build_observations())

    def test_rejects_sys_import(self) -> None:
        code = """
import sys

def strategy(observations, params):
    return [{"action_type": "hold"} for _ in observations]
"""
        with pytest.raises(ValueError, match="disallowed imports"):
            run_python_strategy_with_np(code, _build_observations())

    def test_rejects_from_os_import(self) -> None:
        code = """
from os import path

def strategy(observations, params):
    return [{"action_type": "hold"} for _ in observations]
"""
        with pytest.raises(ValueError, match="disallowed imports"):
            run_python_strategy_with_np(code, _build_observations())

    def test_valid_strategy_with_np_math(self) -> None:
        code = """
import numpy as np

def strategy(observations, params):
    prices = np.array([obs.get("mid_ticks", 100) for obs in observations], dtype=float)
    fast = int(params.get("fast", 20))
    if len(prices) < fast:
        return [{"action_type": "hold"} for _ in observations]
    ma = np.convolve(prices, np.ones(fast) / fast, mode="full")[:len(prices)]
    actions = []
    position = 0
    for i, obs in enumerate(observations):
        if i == 0:
            actions.append({"action_type": "hold"})
            continue
        if prices[i] > ma[i] and position == 0:
            actions.append({"action_type": "market", "side": "buy", "quantity": 100})
            position = 1
        elif prices[i] < ma[i] and position == 1:
            actions.append({"action_type": "market", "side": "sell", "quantity": 100})
            position = 0
        else:
            actions.append({"action_type": "hold"})
    return actions
"""
        result = run_python_strategy_with_np(code, _build_observations(), {"fast": 10, "slow": 20})
        assert result[0]["action_type"] == "hold"
        assert all(item["action_type"] in {"hold", "market"} for item in result)
