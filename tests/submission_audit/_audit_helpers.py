"""Shared panel builders for the submission-audit red-team tests.

These tests assert CORRECT portfolio-accounting behavior. They are EXPECTED TO
FAIL against the current app/strategy_lab/submission/engine.py — each failure
is the proof of a P0 defect. Do not "fix" the tests; fix the engine.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from app.strategy_lab.submission.panels import (
    AssetMetadata,
    DataProvenance,
    MarketDataPanel,
)
from app.strategy_lab.submission.strategy import CrossSectionalSpec


def make_panel(
    close: np.ndarray,
    open_: np.ndarray | None = None,
    benchmark: np.ndarray | None = None,
) -> MarketDataPanel:
    """Build a strict MarketDataPanel from a T x N close matrix."""
    T, N = close.shape
    if open_ is None:
        open_ = close.copy()
    assets = tuple(f"A{i}" for i in range(N))
    dates = tuple(date(2022, 1, 1) + timedelta(days=i) for i in range(T))
    high = np.maximum(open_, close)
    low = np.minimum(open_, close)
    meta = {a: AssetMetadata(ticker=a) for a in assets}
    prov = DataProvenance(source="deterministic_fixture", tier=3, label="audit")
    return MarketDataPanel(
        dates=dates,
        assets=assets,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=np.ones((T, N)),
        benchmark_close=benchmark,
        metadata=meta,
        provenance=prov,
    )


def small_spec(**overrides) -> CrossSectionalSpec:
    """Zero-cost spec with short lookbacks so signals go live quickly.

    Lookbacks 10/2/5 => features valid from t~10; first monthly rebalance with a
    live signal is index 31 (2022-02-01) on a daily-calendar panel starting
    2022-01-01.
    """
    base = dict(
        universe=["A0", "A1"],
        benchmark="A0",
        momentum_lookback=10,
        momentum_short=2,
        volatility_window=5,
        long_quantile=0.5,
        short_quantile=0.5,
        gross_exposure=1.0,
        net_exposure=0.0,
        max_position_weight=1.0,
        commission_bps=0.0,
        spread_bps=0.0,
        slippage_bps=0.0,
        borrow_bps=0.0,
    )
    base.update(overrides)
    return CrossSectionalSpec(**base)


def first_active_target_index(result) -> int:
    """First index t where the engine's active target weights are nonzero."""
    tw = result.target_weights
    for t in range(tw.shape[0]):
        if np.any(np.abs(tw[t]) > 1e-12):
            return t
    raise AssertionError("no nonzero target weights found — panel/spec misconfigured")
