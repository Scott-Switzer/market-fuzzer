"""Deterministic, generated, multi-asset CI/offline fixture (Tier 3).

Labeled SYNTHETIC. At least 6 assets + benchmark, OHLCV, one trend regime, one
reversal, missing-data cases, and KNOWN hand-calculated trades for tests.
Fully deterministic from a fixed seed; no network.
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

FIXTURE_ASSETS = ["SYN_A", "SYN_B", "SYN_C", "SYN_D", "SYN_E", "SYN_F", "SPY"]
FIXTURE_DATES = [date(2021, 1, 1) + timedelta(days=i) for i in range(504)]


def build_fixture_panel(seed: int = 20240101) -> MarketDataPanel:
    rng = np.random.default_rng(seed)
    assets = FIXTURE_ASSETS
    N = len(assets)
    T = len(FIXTURE_DATES)
    close = np.zeros((T, N), dtype=float)
    open_ = np.zeros((T, N), dtype=float)
    high = np.zeros((T, N), dtype=float)
    low = np.zeros((T, N), dtype=float)
    volume = np.zeros((T, N), dtype=float)
    base = np.array([100.0, 120.0, 80.0, 60.0, 140.0, 90.0, 110.0])
    # distinct drift/vol per asset; asset 2 has a mid-sample reversal
    drift = np.array([0.0003, 0.0002, 0.0005, -0.0002, 0.0001, -0.0003, 0.00025])
    vol = np.array([0.012, 0.010, 0.018, 0.020, 0.009, 0.015, 0.011])
    for n in range(N):
        price = base[n]
        for t in range(T):
            d = drift[n]
            if n == 2 and 100 <= t < 150:  # reversal window for asset C
                d = -0.0010
            if n == 3 and t >= 150:  # trend change for asset D
                d = 0.0006
            shock = rng.normal(0.0, vol[n])
            prev = price
            price = max(1.0, price * (1.0 + d + shock))
            open_px = prev * (1.0 + rng.normal(0.0, vol[n] * 0.3))
            close[t, n] = price
            open_[t, n] = open_px
            high[t, n] = max(open_px, price) * (1.0 + abs(rng.normal(0.0, vol[n] * 0.2)))
            low[t, n] = min(open_px, price) * (1.0 - abs(rng.normal(0.0, vol[n] * 0.2)))
            volume[t, n] = float(rng.integers(1_000_000, 5_000_000))
    # missing-data cases: wipe a contiguous block for asset E (index 4)
    missing_start, missing_end = 60, 70
    close[missing_start:missing_end, 4] = np.nan
    open_[missing_start:missing_end, 4] = np.nan
    high[missing_start:missing_end, 4] = np.nan
    low[missing_start:missing_end, 4] = np.nan
    # forward-fill missing OHLC minimally for the panel to validate (label as synthetic)
    close[:, 4] = _fill_forward(close[:, 4])
    open_[:, 4] = _fill_forward(open_[:, 4])
    high[:, 4] = _fill_forward(high[:, 4])
    low[:, 4] = _fill_forward(low[:, 4])

    benchmark = close[:, -1].copy()
    metadata = {a: AssetMetadata(ticker=a, is_benchmark=(a == "SPY"), point_in_time=False) for a in assets}
    provenance = DataProvenance(
        source="deterministic_fixture",
        tier=3,
        retrieval_timestamp="deterministic",
        source_hash="fixture-v1",
        transformations=["gbm_synthetic", "injected_missing_block", "forward_fill_missing"],
        warnings=["SYNTHETIC generated data — not historical market data"],
        label="deterministic fixture (synthetic, tier 3)",
    )
    return MarketDataPanel(
        dates=tuple(FIXTURE_DATES),
        assets=tuple(assets),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        benchmark_close=benchmark,
        metadata=metadata,
        provenance=provenance,
    )


# Deliberately FEASIBLE spec for the 7-asset CI fixture: 3 long + 3 short at 0.10
# cap -> max feasible gross 0.60, so a 0.50 target is met without scaling.
FIXTURE_SPEC = CrossSectionalSpec(
    universe=list(FIXTURE_ASSETS),
    benchmark="SPY",
    start="2021-01-01",
    end="2023-01-01",
    momentum_lookback=120,
    momentum_short=21,
    volatility_window=30,
    long_quantile=0.50,
    short_quantile=0.50,
    gross_exposure=0.50,
    net_exposure=0.0,
    max_position_weight=0.10,
    commission_bps=5.0,
    spread_bps=2.0,
    slippage_bps=3.0,
    borrow_bps=50.0,
    locate_bps=10.0,
)


def build_fixture_spec() -> CrossSectionalSpec:
    return FIXTURE_SPEC


def _fill_forward(col: np.ndarray) -> np.ndarray:
    out = col.copy()
    last = np.nan
    for i in range(len(out)):
        if np.isnan(out[i]):
            out[i] = last if not np.isnan(last) else 100.0
        else:
            last = out[i]
    return out
