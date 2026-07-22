from __future__ import annotations

import hashlib
import json
import logging
import warnings
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import numpy as np

from app.break_test.exchange_fwd import (
    _build_world,
    _build_one_factor_paths_for_assets,
    _build_correlated_synthetic_paths,
    DEFAULT_ASSETS,
    EXPANDED_UNIVERSE_PRESETS,
    UserStrategyOrderRouter,
    build_world,
    run_exchange_forward_test,
)
from app.break_test.metrics import backtest_metrics, compute_equity_curve
from app.break_test.regimes import detect_regimes, run_forward_test
from app.break_test.reporting import build_failure_report
from app.break_test.strategies import BUILTIN_STRATEGIES, compute_positions
from app.simulation import run_simulation
from app.schemas import (
    AgentPopulation,
    AgentsSpec,
    AssetSpec,
    ClockSpec,
    ExchangeSpec,
    ExperimentSpec,
    InterventionSpec,
    MacroSpec,
    ParentOrderSpec,
    WorldSpec,
)

if TYPE_CHECKING:
    from app.break_test.costs import TransactionCostModel

from typing import cast

logger = logging.getLogger(__name__)

_MIN_BARS = 252 * 5
_DEFAULT_LOOKBACK = "5y"
_RECOMMENDED_LOOKBACK = "20y"
_SESSION_STORE: dict[str, dict[str, Any]] = {}


def _stable_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def get_available_strategies() -> dict[str, dict[str, object]]:
    return BUILTIN_STRATEGIES


def _warn_short_history(closes: list[float]) -> dict[str, object] | None:
    if len(closes) < _MIN_BARS:
        years = len(closes) / 252.0
        message = (
            f"Input history is {len(closes)} bars (~{years:.1f} years). "
            f"Consider sourcing {_RECOMMENDED_LOOKBACK} of market history for robust quant break testing."
        )
        warnings.warn(message, stacklevel=2)
        return {
            "short_history": True,
            "bars": len(closes),
            "years": round(years, 2),
            "min_bars": _MIN_BARS,
            "message": message,
            "suggested_lookback": _RECOMMENDED_LOOKBACK,
        }
    return {"short_history": False}


def run_break_test(
    closes: list[float],
    strategy_type: str,
    params: dict[str, int] | None = None,
    worlds_per_regime: int = 100,
    fix_and_retest_params: dict[str, int] | None = None,
    forward_mode: str = "gbm",
    strategy_code: str | None = None,
    plain_english: str | None = None,
    tcost_model: "TransactionCostModel | None" = None,
    default_adv: float | None = None,
    data_source: str | None = None,
    lookback_period: str | None = None,
    universe_preset: str | None = None,
    asset_count: int | None = None,
    fred_series: list[str] | None = None,
    fred: dict[str, Any] | None = None,
    regime_hints: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, object]:
    prices = np.asarray(closes, dtype=float)
    if prices.size == 0:
        try:
            from app.break_test.data_loader import load_yfinance, load_yfinance_bulk

            if data_source == "yfinance":
                period = lookback_period or _DEFAULT_LOOKBACK
                prices = np.asarray(load_yfinance("SPY", period=period), dtype=float)
        except Exception as exc:
            logger.debug("Auto-close fallback skipped: %s", exc)
    resolved_params: dict[str, int] = _resolve_params(strategy_type, params)
    if strategy_type == "plain_english" and not strategy_code:
        text = (plain_english or "").strip()
        if text:
            try:
                from app.break_test.strategy_compiler import classify_strategy

                compiled = classify_strategy(text)
                strategy_type = str(compiled.get("template_key", strategy_type))
                resolved_params = dict(compiled.get("defaults", {}))
                strategy_code = str(compiled.get("code", ""))
            except Exception:
                pass
    validation = validation if isinstance(validation, dict) else _warn_short_history(prices.tolist())
    _validate_prices(prices, strategy_type, resolved_params)

    if strategy_code:
        historical, equity_curve, regime_analysis, forward_results = _run_custom_strategy(
            strategy_code, strategy_type, prices, resolved_params, worlds_per_regime, forward_mode, tcost_model, default_adv
        )
    else:
        tcost = _resolve_tcost_model(tcost_model)
        historical_positions = compute_positions(strategy_type, prices, **resolved_params)
        historical = backtest_metrics(
            prices,
            historical_positions,
            tcost_model=tcost,
            default_adv=default_adv,
        )
        equity_curve = compute_equity_curve(
            prices,
            historical_positions,
            tcost_model=tcost,
            default_adv=default_adv,
        )
        regime_analysis = detect_regimes(prices)
        regime_analysis = _merge_regime_hints(regime_analysis, regime_hints, fred, validation)

        if forward_mode == "exchange":
            forward_results = run_exchange_forward_test(
                closes, strategy_type, resolved_params, worlds_per_regime
            )
        else:
            forward_results = run_forward_test(prices, strategy_type, resolved_params, worlds_per_regime)

    report = build_failure_report(strategy_type, resolved_params, historical, forward_results)

    corrected: dict[str, object] | None = None
    if fix_and_retest_params:
        if strategy_code:
            corr_historical, corr_equity, _, corr_forward = _run_custom_strategy(
                strategy_code, strategy_type, prices, fix_and_retest_params, worlds_per_regime, forward_mode, tcost_model, default_adv
            )
        else:
            tcost = _resolve_tcost_model(tcost_model)
            corr_positions = compute_positions(strategy_type, prices, **fix_and_retest_params)
            corr_historical = backtest_metrics(
                prices,
                corr_positions,
                tcost_model=tcost,
                default_adv=default_adv,
            )
            corr_equity = compute_equity_curve(
                prices,
                corr_positions,
                tcost_model=tcost,
                default_adv=default_adv,
            )
            corr_forward = (
                run_exchange_forward_test(closes, strategy_type, fix_and_retest_params, worlds_per_regime)
                if forward_mode == "exchange"
                else run_forward_test(prices, strategy_type, fix_and_retest_params, worlds_per_regime)
            )
        corrected = build_failure_report(strategy_type, fix_and_retest_params, corr_historical, corr_forward)

    session_id = str(uuid4())
    result: dict[str, object] = {
        "session_id": session_id,
        "strategy": {"type": strategy_type, "parameters": resolved_params},
        "historical": historical,
        "equity_curve": equity_curve,
        "regime_analysis": regime_analysis,
        "forward_test": report["forward_test"],
        "forward_mode": forward_mode,
        "failure_summary": report["failure_summary"],
        "failure_analysis": report.get("failure_analysis"),
        "correction_suggestion": report["correction_suggestion"],
        "corrected": corrected,
        "limitations": _merge_limitations(report.get("limitations"), validation, data_source, lookback_period, fred),
    }
    if universe_preset or asset_count:
        result["universe"] = {
            "preset": universe_preset,
            "asset_count": asset_count,
        }
    _SESSION_STORE[session_id] = result
    return result


def get_session(session_id: str) -> dict[str, Any] | None:
    return _SESSION_STORE.get(session_id)


def _resolve_params(strategy_type: str, params: dict[str, int] | None) -> dict[str, int]:
    defaults = cast("dict[str, int] | None", BUILTIN_STRATEGIES.get(strategy_type, {}).get("default_params"))
    resolved = dict(defaults) if defaults else {}
    if params:
        resolved.update(params)
    return resolved


def _validate_prices(prices: np.ndarray, strategy_type: str, params: dict[str, int]) -> None:
    min_len = max(
        80,
        params.get("slow", 50) + 2,
        params.get("entry_lookback", 20) + 2,
        params.get("period", 14) + 2,
    )
    if len(prices) < min_len or not np.all(np.isfinite(prices)) or np.any(prices <= 0):
        raise ValueError(f"Provide at least {min_len} finite, positive closing prices")


def _resolve_tcost_model(tcost_model: "TransactionCostModel | None") -> "TransactionCostModel | None":
    if tcost_model is not None:
        return tcost_model
    try:
        from app.break_test.costs import TransactionCostModel

        return TransactionCostModel()
    except Exception:
        return None


def _merge_regime_hints(
    regime_analysis: dict[str, Any],
    regime_hints: dict[str, Any] | None,
    fred: dict[str, Any] | None,
    validation: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(regime_analysis)
    if regime_hints:
        merged["macro_regime_hints"] = regime_hints
        merged["regime"] = str(regime_hints.get("regime", merged.get("regime")))
    if fred:
        merged["macro_fred"] = fred
    if validation and validation.get("short_history"):
        merged["history_validation"] = validation
    return merged


def _merge_limitations(
    limitations: dict[str, Any] | None,
    validation: dict[str, Any] | None,
    data_source: str | None,
    lookback_period: str | None,
    fred: dict[str, Any] | None,
) -> dict[str, Any]:
    merged: dict[str, Any] = dict(limitations if isinstance(limitations, dict) else {})
    if validation and validation.get("short_history"):
        merged.setdefault("input_warnings", [])
        merged["input_warnings"] = list(merged["input_warnings"]) + [validation.get("message", "Short history")]
    if data_source:
        merged["data_source"] = data_source
    if lookback_period:
        merged["lookback_period"] = lookback_period
    if fred:
        merged["fred_series_count"] = len(fred)
    return merged


def _run_custom_strategy(
    code: str,
    strategy_type: str,
    prices: np.ndarray,
    params: dict[str, int],
    worlds_per_regime: int,
    forward_mode: str,
    tcost_model: "TransactionCostModel | None" = None,
    default_adv: float | None = None,
) -> tuple[dict[str, float | int], list[float], dict[str, object], list[dict[str, object]]]:
    from app.break_test.python_runner import run_python_strategy_with_np

    observations = [
        {
            "step": i,
            "symbol": "ASSET",
            "side": "buy",
            "mid_ticks": int(prices[i]),
            "best_bid_ticks": int(prices[i] * 0.999),
            "best_ask_ticks": int(prices[i] * 1.001),
            "spread_bps": 2.0,
            "observed_volume": 100_000,
            "inventory": 0,
            "remaining_quantity": 0,
            "exchange_latency_profile": "normal",
            "intervention_active": False,
        }
        for i in range(len(prices))
    ]
    observations_from = list(observations)
    actions = run_python_strategy_with_np(code, observations_from, params)
    positions = np.zeros(len(prices))
    for i, action in enumerate(actions):
        if i >= len(positions):
            break
        if action.get("action_type") == "market" and action.get("side") == "buy":
            positions[i] = 1.0
        elif action.get("action_type") == "market" and action.get("side") == "sell":
            positions[i] = 0.0
        elif i > 0:
            positions[i] = positions[i - 1]

    tcost = _resolve_tcost_model(tcost_model)
    historical = backtest_metrics(prices, positions, tcost_model=tcost, default_adv=default_adv)
    equity_curve = compute_equity_curve(prices, positions, tcost_model=tcost, default_adv=default_adv)
    regime_analysis = detect_regimes(prices)
    forward_results = _run_custom_forward_test(
        prices, code, params, worlds_per_regime, forward_mode, tcost_model, default_adv
    )
    return historical, equity_curve, regime_analysis, forward_results


def _run_custom_forward_test(
    prices: np.ndarray,
    code: str,
    params: dict[str, int],
    worlds_per_regime: int,
    forward_mode: str,
    tcost_model: "TransactionCostModel | None" = None,
    default_adv: float | None = None,
) -> list[dict[str, object]]:
    from app.break_test.python_runner import run_python_strategy_with_np

    if forward_mode == "exchange":
        return _run_custom_exchange_forward_test(prices, code, params, worlds_per_regime)

    base_returns = np.diff(np.log(prices))
    base_vol = max(float(np.std(base_returns)), 0.0001)
    regime_specs = {
        "Steady Trend": (0.0005, 0.7, 0.0),
        "High Volatility": (0.0003, 1.6, 0.02),
        "Low-Liquidity Chop": (-0.0001, 1.2, 0.01),
        "Crisis Drawdown": (-0.002, 2.2, 0.05),
    }

    def sample_world(regime_name: str, seed: int) -> tuple[np.ndarray, float]:
        drift, vol_mult, gap_prob = regime_specs[regime_name]
        rng = np.random.default_rng(seed)
        shock = float(rng.choice([-1.0, 0.0, 1.0], p=[gap_prob / 2, 1 - gap_prob, gap_prob / 2]))
        returns = rng.normal(drift, base_vol * vol_mult, size=len(prices) - 1)
        if shock:
            returns[int(rng.integers(0, len(returns)))] += shock * np.mean(np.abs(returns)) * 3
        sampled = prices[0] * np.exp(np.cumsum(returns))
        return sampled, float(np.mean(np.abs(returns)))

    results: list[dict[str, object]] = []
    for regime_name, count in zip(regime_specs, [worlds_per_regime // 4] * 4):
        sampled_worlds = []
        for idx in range(max(1, count)):
            sampled, adv = sample_world(regime_name, idx * 1000 + hash(regime_name) % 997)
            sampled_worlds.append((sampled, adv))
        world_losses = []
        world_returns = []
        worst_dd = 0.0
        tcost = _resolve_tcost_model(tcost_model)
        for sampled, adv in sampled_worlds:
            try:
                world_positions = _custom_positions_from_code(prices, code, params)
                metrics = backtest_metrics(sampled, world_positions, tcost_model=tcost, default_adv=default_adv or adv)
            except Exception:
                metrics = {
                    "total_return_pct": 0.0,
                    "max_drawdown_pct": 0.0,
                    "win_rate_pct": 0.0,
                    "expectancy": 0.0,
                }
            world_losses.append(1 if float(metrics["total_return_pct"]) < 0 else 0)
            world_returns.append(metrics["total_return_pct"])
            if float(metrics["max_drawdown_pct"]) < worst_dd:
                worst_dd = float(metrics["max_drawdown_pct"])
        results.append(
            {
                "regime": regime_name,
                "regime_num": len(results) + 1,
                "worlds": count,
                "loss_rate_pct": round(sum(world_losses) / len(world_losses) * 100, 2) if world_losses else 0.0,
                "median_return_pct": round(float(np.median(world_returns)), 2) if world_returns else 0.0,
                "worst_drawdown_pct": round(float(worst_dd), 2),
                "best_return_pct": round(float(np.max(world_returns)), 2) if world_returns else 0.0,
            }
        )
    return results


def _run_custom_exchange_forward_test(
    prices: np.ndarray,
    code: str,
    params: dict[str, int],
    worlds_per_regime: int,
    tcost_model: "TransactionCostModel | None" = None,
    default_adv: float | None = None,
) -> list[dict[str, object]]:
    from app.break_test.python_runner import run_python_strategy_with_np

    observations = []
    for i, px in enumerate(prices.tolist()):
        observations.append(
            {
                "step": i,
                "symbol": "ASSET",
                "side": "buy",
                "mid_ticks": int(px),
                "best_bid_ticks": int(px * 0.999),
                "best_ask_ticks": int(px * 1.001),
                "spread_bps": 2.0,
                "observed_volume": max(50_000, float(prices.sum()) * 0.01),
                "inventory": 0,
                "remaining_quantity": 0,
                "exchange_latency_profile": "normal",
                "intervention_active": False,
            }
        )
    actions = run_python_strategy_with_np(code, observations, params)
    positions = np.zeros(len(observations), dtype=float)
    for i, action in enumerate(actions):
        if i >= len(positions):
            break
        if action.get("action_type") == "market" and action.get("side") == "buy":
            positions[i] = 1.0
        elif action.get("action_type") == "market" and action.get("side") == "sell":
            positions[i] = 0.0
        elif i > 0:
            positions[i] = positions[i - 1]
    tcost = _resolve_tcost_model(tcost_model)
    metrics = backtest_metrics(prices, positions, tcost_model=tcost, default_adv=default_adv)
    regime = detect_regimes(prices)
    return [
        {
            "regime": regime.get("regime", "normal-vol / mixed"),
            "regime_num": 1,
            "worlds": worlds_per_regime,
            "loss_rate_pct": 1 if float(metrics["total_return_pct"]) < 0 else 0,
            "median_return_pct": round(float(metrics["total_return_pct"]), 2),
            "worst_drawdown_pct": round(float(metrics["max_drawdown_pct"]), 2),
            "best_return_pct": round(float(metrics["total_return_pct"]), 2),
        }
    ]


def _custom_positions_from_code(prices: np.ndarray, code: str, params: dict[str, int]) -> np.ndarray:
    from app.break_test.python_runner import run_python_strategy_with_np

    observations = []
    for i, px in enumerate(prices.tolist()):
        observations.append(
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
        )
    actions = run_python_strategy_with_np(code, observations, params)
    positions = np.zeros(len(observations), dtype=float)
    for i, action in enumerate(actions):
        if i >= len(positions):
            break
        if action.get("action_type") == "market" and action.get("side") == "buy":
            positions[i] = 1.0
        elif action.get("action_type") == "market" and action.get("side") == "sell":
            positions[i] = 0.0
        elif i > 0:
            positions[i] = positions[i - 1]
    return positions
