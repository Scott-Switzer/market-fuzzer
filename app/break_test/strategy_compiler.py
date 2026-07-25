from __future__ import annotations

from typing import Any


class StrategyCompiler:
    _ORDERED_CLUSTERS: list[tuple[str, tuple[str, ...]]] = [
        (
            "pairs_relative_value",
            (
                "pairs trade",
                "pair trade",
                "relative value",
                "spread between",
                "z-score spread",
                "cointegration",
                "convergence",
                "divergence",
                "two assets",
                "two symbols",
                "hedged pair",
                "statistical arbitrage pair",
                "ratio",
                "long one short the other",
                "market neutral pair",
                "paired",
                "pairing",
            ),
        ),
        (
            "volatility_compression",
            (
                "volatility compression",
                "low volatility",
                "tight range",
                "coil",
                "squeeze",
                "bollinger contraction",
                "narrowing band",
                "range compression",
                "quiet before",
                "breakout from tight range",
                "low atr",
                "compress",
            ),
        ),
        (
            "breakout_follow",
            (
                "breakout",
                "new high",
                "recent high",
                "breaks above",
                "break above",
                "entry",
                "breakout pullback",
                "range breakout",
                "channel breakout",
            ),
        ),
        (
            "trending_momentum",
            (
                "trend",
                "moving average",
                "momentum",
                "follow",
                "above",
                "crosses above",
                "rising",
                "directional",
                "trend following",
                "higher highs",
                "price above average",
            ),
        ),
        (
            "mean_reversion",
            (
                "mean reversion",
                "oversold",
                "overbought",
                "bounce",
                "revert",
                "return to normal",
                "drops and rises",
                "reversal",
                "fades",
                "extreme",
                "overextended",
                "bollinger band",
                "deviates",
                "mean revert",
            ),
        ),
    ]

    _PREFIX = "def strategy(observations, params):\n    "

    _TEMPLATES: dict[str, str] = {
        "trending_momentum": (
            _PREFIX
            + """prices = np.array([obs.get("mid_ticks", obs.get("close", 100)) for obs in observations], dtype=float)
    fast = int(params.get("fast", 20))
    slow = int(params.get("slow", 50))
    if len(prices) < slow:
        return [{"action_type": "hold"} for _ in observations]
    fast_ma = np.convolve(prices, np.ones(fast) / fast, mode="full")[: len(prices)]
    slow_ma = np.convolve(prices, np.ones(slow) / slow, mode="full")[: len(prices)]
    actions = []
    position = 0
    for i, obs in enumerate(observations):
        if i == 0:
            actions.append({"action_type": "hold"})
            continue
        if fast_ma[i] > slow_ma[i] and position == 0:
            actions.append({"action_type": "market", "side": "buy", "quantity": 100})
            position = 1
        elif fast_ma[i] < slow_ma[i] and position == 1:
            actions.append({"action_type": "market", "side": "sell", "quantity": 100})
            position = 0
        else:
            actions.append({"action_type": "hold"})
    return actions
"""
        ),
        "mean_reversion": (
            _PREFIX
            + """prices = np.array([obs.get("mid_ticks", obs.get("close", 100)) for obs in observations], dtype=float)
    lookback = int(params.get("lookback", 20))
    threshold = float(params.get("threshold", 1.5))
    actions = []
    position = 0
    for i, obs in enumerate(observations):
        if i < lookback:
            actions.append({"action_type": "hold"})
            continue
        window = prices[i - lookback : i]
        mean = float(np.mean(window))
        std = float(np.std(window))
        z = (prices[i] - mean) / std if std > 1e-9 else 0.0
        if z < -threshold and position == 0:
            actions.append({"action_type": "market", "side": "buy", "quantity": 100})
            position = 1
        elif z > threshold and position == 1:
            actions.append({"action_type": "market", "side": "sell", "quantity": 100})
            position = 0
        else:
            actions.append({"action_type": "hold"})
    return actions
"""
        ),
        "breakout_follow": (
            _PREFIX
            + """prices = np.array([obs.get("mid_ticks", obs.get("close", 100)) for obs in observations], dtype=float)
    lookback = int(params.get("lookback", 20))
    hold = int(params.get("hold", 10))
    actions = []
    entry_price = None
    hold_counter = 0
    for i, obs in enumerate(observations):
        if entry_price is None and i >= lookback:
            if prices[i] >= float(np.max(prices[i - lookback : i])):
                actions.append({"action_type": "market", "side": "buy", "quantity": 100})
                entry_price = float(prices[i])
                hold_counter = hold
                continue
        if entry_price is not None:
            hold_counter -= 1
            if hold_counter <= 0:
                actions.append({"action_type": "market", "side": "sell", "quantity": 100})
                entry_price = None
                continue
        actions.append({"action_type": "hold"})
    return actions
"""
        ),
        "volatility_compression": (
            _PREFIX
            + """prices = np.array([obs.get("mid_ticks", obs.get("close", 100)) for obs in observations], dtype=float)
    lookback = int(params.get("lookback", 40))
    low_vol = float(params.get("low_vol", 0.6))
    exit_bars = int(params.get("exit_bars", 12))
    if len(prices) < lookback + 1:
        return [{"action_type": "hold"} for _ in observations]
    rets = np.diff(np.log(prices))
    vol = np.full(len(prices), np.nan, dtype=float)
    if len(rets) >= lookback:
        vol[-len(rets) :] = np.convolve(rets**2, np.ones(lookback) / lookback, mode="full")[: len(rets)]
    actions = []
    position = 0
    timer = 0
    for i, obs in enumerate(observations):
        if i == 0:
            actions.append({"action_type": "hold"})
            continue
        cur_vol = float(vol[i - 1]) if i > 0 and np.isfinite(vol[i - 1]) else None
        if position == 0 and cur_vol is not None and cur_vol < low_vol:
            actions.append({"action_type": "market", "side": "buy", "quantity": 100})
            position = 1
            timer = exit_bars
        elif position == 1:
            timer -= 1
            if timer <= 0:
                actions.append({"action_type": "market", "side": "sell", "quantity": 100})
                position = 0
            else:
                actions.append({"action_type": "hold"})
        else:
            actions.append({"action_type": "hold"})
    return actions
"""
        ),
        "pairs_relative_value": (
            _PREFIX
            + """symbol_a_prices = np.array([obs.get("mid_ticks", obs.get("close", 100)) for obs in observations], dtype=float)
    symbol_b_prices = np.array([obs.get("pair_mid_ticks", symbol_a_prices[i]) for i, obs in enumerate(observations)], dtype=float)
    lookback = int(params.get("lookback", 40))
    z_entry = float(params.get("z_entry", 1.8))
    z_exit = float(params.get("z_exit", 0.4))
    if len(symbol_a_prices) < lookback + 1:
        return [{"action_type": "hold"} for _ in observations]
    spread = np.log(symbol_a_prices) - np.log(symbol_b_prices)
    spread_mean = np.convolve(spread, np.ones(lookback) / lookback, mode="full")[: len(spread)]
    spread_std = np.full(len(spread), np.nan, dtype=float)
    raw_rets = spread - spread_mean
    if len(raw_rets) >= lookback:
        spread_std[-len(raw_rets) :] = np.convolve(raw_rets**2, np.ones(lookback) / lookback, mode="full")[: len(raw_rets)]
    actions = []
    position = 0
    for i, obs in enumerate(observations):
        if i < lookback or not np.isfinite(spread_std[i]):
            actions.append({"action_type": "hold"})
            continue
        z = (spread[i] - spread_mean[i]) / (spread_std[i] ** 0.5) if spread_std[i] > 1e-9 else 0.0
        if z < -z_entry and position == 0:
            actions.append({"action_type": "market", "side": "buy", "quantity": 100})
            position = 1
        elif z > z_entry and position == 0:
            actions.append({"action_type": "market", "side": "sell", "quantity": 100})
            position = -1
        elif position == 1 and z > -z_exit:
            actions.append({"action_type": "market", "side": "sell", "quantity": 100})
            position = 0
        elif position == -1 and z < z_exit:
            actions.append({"action_type": "market", "side": "buy", "quantity": 100})
            position = 0
        else:
            actions.append({"action_type": "hold"})
    return actions
"""
        ),
    }

    _DEFAULTS: dict[str, dict[str, Any]] = {
        "trending_momentum": {"fast": 20, "slow": 50},
        "mean_reversion": {"lookback": 20, "threshold": 1.5},
        "breakout_follow": {"lookback": 20, "hold": 10},
        "volatility_compression": {"lookback": 40, "low_vol": 0.6, "exit_bars": 12},
        "pairs_relative_value": {"lookback": 40, "z_entry": 1.8, "z_exit": 0.4},
    }

    _DESCRIPTIONS: dict[str, str] = {
        "trending_momentum": "Buy when price is above its moving average and exit when it falls below.",
        "mean_reversion": "Buy after drops and sell after rises, expecting a return to normal.",
        "breakout_follow": "Buy when price breaks above the recent high, then exit after a fixed hold.",
        "volatility_compression": "Buy when realized volatility contracts to a low threshold, then exit after a fixed timer.",
        "pairs_relative_value": "Trade the normalized spread between two symbols, buying the weaker leg when deviation is extreme.",
    }

    _TEMPLATE_META: dict[str, dict[str, Any]] = {
        "trending_momentum": {
            "meta_key": "trending_momentum",
            "inputs": [
                {"name": "prices", "source": "observations[*].mid_ticks or close", "shape": "[T]"},
                {"name": "fast", "source": "params.fast", "type": "int", "default": 20},
                {"name": "slow", "source": "params.slow", "type": "int", "default": 50},
            ],
            "outputs": [{"name": "actions", "type": "list[dict]", "length": "len(observations)"}],
            "expected_observations": [
                "Long-biased signals in uptrends when fast MA > slow MA.",
                "Position flips to flat when fast MA crosses below slow MA.",
                "If prices < slow, output holds for the entire window.",
            ],
        },
        "mean_reversion": {
            "meta_key": "mean_reversion",
            "inputs": [
                {"name": "prices", "source": "observations[*].mid_ticks or close", "shape": "[T]"},
                {"name": "lookback", "source": "params.lookback", "type": "int", "default": 20},
                {"name": "threshold", "source": "params.threshold", "type": "float", "default": 1.5},
            ],
            "outputs": [{"name": "actions", "type": "list[dict]", "length": "len(observations)"}],
            "expected_observations": [
                "No action until a full lookback window is available.",
                "Buy when z-score is deeply negative; sell when deeply positive.",
                "Works best when price reverts after sharp drops or spikes.",
            ],
        },
        "breakout_follow": {
            "meta_key": "breakout_follow",
            "inputs": [
                {"name": "prices", "source": "observations[*].mid_ticks or close", "shape": "[T]"},
                {"name": "lookback", "source": "params.lookback", "type": "int", "default": 20},
                {"name": "hold", "source": "params.hold", "type": "int", "default": 10},
            ],
            "outputs": [{"name": "actions", "type": "list[dict]", "length": "len(observations)"}],
            "expected_observations": [
                "Signal only after lookback bars are available.",
                "Entry on new high breakout, then fixed holding period.",
                "Strategy can underperform in choppy, range-bound markets.",
            ],
        },
        "volatility_compression": {
            "meta_key": "volatility_compression",
            "inputs": [
                {"name": "prices", "source": "observations[*].mid_ticks or close", "shape": "[T]"},
                {"name": "lookback", "source": "params.lookback", "type": "int", "default": 40},
                {"name": "low_vol", "source": "params.low_vol", "type": "float", "default": 0.6},
                {"name": "exit_bars", "source": "params.exit_bars", "type": "int", "default": 12},
            ],
            "outputs": [{"name": "actions", "type": "list[dict]", "length": "len(observations)"}],
            "expected_observations": [
                "Waits for realized volatility to compress below a threshold.",
                "Enters long briefly, then exits after a fixed timer.",
                "Only produces entries after lookback + 1 observations.",
            ],
        },
        "pairs_relative_value": {
            "meta_key": "pairs_relative_value",
            "inputs": [
                {"name": "symbol_a_prices", "source": "observations[*].mid_ticks or close", "shape": "[T]"},
                {"name": "symbol_b_prices", "source": "observations[*].pair_mid_ticks", "shape": "[T]"},
                {"name": "lookback", "source": "params.lookback", "type": "int", "default": 40},
                {"name": "z_entry", "source": "params.z_entry", "type": "float", "default": 1.8},
                {"name": "z_exit", "source": "params.z_exit", "type": "float", "default": 0.4},
            ],
            "outputs": [{"name": "actions", "type": "list[dict]", "length": "len(observations)"}],
            "expected_observations": [
                "Requires pair_mid_ticks in each observation; otherwise falls back to a single-symbol degenerate case.",
                "Uses log-spread z-score with rolling mean/std.",
                "Supports directional exposure: long A / short B or inverse, then flat on convergence.",
            ],
        },
    }

    @staticmethod
    def normalize_input(raw_input: Any) -> str:
        if raw_input is None:
            return ""
        text = raw_input.strip() if isinstance(raw_input, str) else str(raw_input).strip()
        return " ".join(text.split())

    @classmethod
    def route(cls, text: str) -> dict[str, Any]:
        lowered = text.lower()
        for key, keywords in cls._ORDERED_CLUSTERS:
            if any(keyword in lowered for keyword in keywords):
                return {"template_key": key, "confidence": "high", "match_method": "ordered_cluster"}
        return {
            "template_key": "trending_momentum",
            "confidence": "low",
            "match_method": "default_fallback",
        }

    @classmethod
    def classify(cls, text: str) -> dict[str, Any]:
        route_info = cls.route(text)
        key = route_info["template_key"]
        template = cls._TEMPLATES[key]
        defaults = dict(cls._DEFAULTS.get(key, {}))
        meta = dict(cls._TEMPLATE_META.get(key, {}))
        template_code = {
            "template_key": key,
            "meta_key": meta.get("meta_key", key),
            "inputs": meta.get("inputs", []),
            "outputs": meta.get("outputs", []),
            "expected_observations": meta.get("expected_observations", []),
        }
        return {
            "template_key": key,
            "confidence": route_info["confidence"],
            "match_method": route_info["match_method"],
            "name": cls._format_name(key),
            "description": cls._DESCRIPTIONS.get(key, key.replace("_", " ")),
            "defaults": defaults,
            "code": template,
            "template_code": template_code,
        }

    @staticmethod
    def _format_name(template: str) -> str:
        return template.replace("_", " ").title()


def normalize_plain_english_input(raw_input: Any) -> str:
    return StrategyCompiler.normalize_input(raw_input)


def classify_strategy(text: str) -> dict[str, Any]:
    return StrategyCompiler.classify(StrategyCompiler.normalize_input(text))


def available_templates() -> dict[str, dict[str, object]]:
    return {
        key: {
            "name": StrategyCompiler._format_name(key),
            "description": StrategyCompiler._DESCRIPTIONS.get(key, key),
            "defaults": dict(StrategyCompiler._DEFAULTS.get(key, {})),
            "meta_key": StrategyCompiler._TEMPLATE_META.get(key, {}).get("meta_key", key),
            "inputs": StrategyCompiler._TEMPLATE_META.get(key, {}).get("inputs", []),
            "outputs": StrategyCompiler._TEMPLATE_META.get(key, {}).get("outputs", []),
            "expected_observations": StrategyCompiler._TEMPLATE_META.get(key, {}).get(
                "expected_observations", []
            ),
        }
        for key in StrategyCompiler._TEMPLATES
    }
