"""Shared fixtures for strategy executor / compiler tests."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from app.strategy_lab.submission.panels import AssetMetadata, DataProvenance, MarketDataPanel


def make_panel(
    assets: list[str],
    close: np.ndarray,
    *,
    open_: np.ndarray | None = None,
    start: date = date(2019, 1, 1),
    benchmark_close: np.ndarray | None = None,
) -> MarketDataPanel:
    """Build a MarketDataPanel from a T x N close matrix with daily calendar dates."""
    T, N = close.shape
    assert len(assets) == N
    dates = tuple(start + timedelta(days=i) for i in range(T))
    op = open_ if open_ is not None else close * 0.999
    return MarketDataPanel(
        dates=dates,
        assets=tuple(assets),
        open=op,
        high=np.maximum(close, op) * 1.001,
        low=np.minimum(close, op) * 0.999,
        close=close,
        volume=np.ones((T, N)) * 1e6,
        benchmark_close=benchmark_close,
        metadata={a: AssetMetadata(a) for a in assets},
        provenance=DataProvenance(source="deterministic_fixture", tier=3),
    )


def gbm_panel(
    assets: list[str], T: int = 400, seed: int = 1, drift: float = 0.0003, vol: float = 0.012
) -> MarketDataPanel:
    rng = np.random.default_rng(seed)
    cols = [100 * np.cumprod(1 + rng.normal(drift + 0.00005 * k, vol, T)) for k, _ in enumerate(assets)]
    close = np.column_stack(cols)
    return make_panel(assets, close, benchmark_close=close[:, 0].copy())


@pytest.fixture
def two_asset_panel():
    # deterministic trending SPY + slow AGG, 300 bars
    T = 300
    rng = np.random.default_rng(7)
    spy = 100 * np.cumprod(1 + rng.normal(0.0004, 0.01, T))
    agg = 100 * np.cumprod(1 + rng.normal(0.0001, 0.003, T))
    close = np.column_stack([spy, agg])
    return make_panel(["VOO", "BND"], close, benchmark_close=spy.copy())
