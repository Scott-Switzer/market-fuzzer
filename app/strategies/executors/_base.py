"""Shared executor helpers: panel <-> context, cost model extraction, weight helpers."""

from __future__ import annotations

import numpy as np

from app.domain.strategy_spec import StrategySpec
from app.strategies.accounting import CostModel
from app.strategies.contracts import StrategyExecutionContext


def context_from_panel(panel: object) -> StrategyExecutionContext:
    """Build a StrategyExecutionContext from a MarketDataPanel-like object.

    Accepts anything exposing ``dates``, ``assets``, ``open``, ``close``,
    ``benchmark_close`` (the submission ``MarketDataPanel``).
    """
    return StrategyExecutionContext(
        dates=np.asarray(panel.dates, dtype=object),  # type: ignore[attr-defined]
        assets=tuple(panel.assets),  # type: ignore[attr-defined]
        open=np.asarray(panel.open, dtype=float),  # type: ignore[attr-defined]
        close=np.asarray(panel.close, dtype=float),  # type: ignore[attr-defined]
        benchmark_close=(
            None
            if getattr(panel, "benchmark_close", None) is None
            else np.asarray(panel.benchmark_close, dtype=float)  # type: ignore[attr-defined]
        ),
        data_provenance={
            "source": getattr(getattr(panel, "provenance", None), "source", "unknown"),
        },
    )


def cost_model_from_spec(spec: StrategySpec) -> CostModel:
    cm = spec.cost_model
    return CostModel(
        commission_bps=float(cm.commission_bps),
        spread_bps=float(cm.spread_bps),
        slippage_bps=float(cm.slippage_bps),
        borrow_bps=float(cm.borrow_bps_annual),
        locate_bps=float(cm.locate_bps),
        model_type=cm.model_type,
        calibrated=cm.calibrated,
    )


def tradable_mask(spec: StrategySpec, assets: tuple[str, ...]) -> np.ndarray:
    """Boolean mask of assets that are tradable members of the strategy universe.

    Excludes the benchmark unless ``benchmark_tradable`` is set.
    """
    uni = set(spec.universe)
    out = np.array([a in uni for a in assets], dtype=bool)
    if spec.benchmark and not spec.benchmark_tradable:
        for i, a in enumerate(assets):
            if a == spec.benchmark:
                out[i] = False
    return out


def signal_lookback_bars(spec: StrategySpec) -> int:
    """Max warmup bar count implied by the spec's signal definitions."""
    from app.domain.strategy_spec import SignalDefinition  # noqa: F401

    max_warmup = 2
    for sig in spec.signal_definitions:
        if sig.kind == "sma":
            max_warmup = max(max_warmup, int(sig.slow_window) + 1)
        elif sig.kind == "momentum":
            # 12-1 momentum: lookback + skip + 1 execution bar
            max_warmup = max(max_warmup, int(sig.lookback) + int(sig.skip) + 1)
        elif sig.kind == "realized_volatility":
            max_warmup = max(max_warmup, int(sig.lookback) + 1)
        elif sig.kind == "relative_momentum":
            max_warmup = max(max_warmup, int(sig.lookback) + 1)
        elif sig.kind == "trend_filter":
            max_warmup = max(max_warmup, int(sig.window) + 1)
    return max_warmup


__all__ = [
    "context_from_panel",
    "cost_model_from_spec",
    "tradable_mask",
    "signal_lookback_bars",
]
