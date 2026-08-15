"""Deterministic synthetic fixture provider (Phase 3).

Explicitly labeled synthetic data for CI and local onboarding. Requires
``allow_synthetic_fixture=True`` in the request. Never presented as historical.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np

from app.market_data.adjustments import AdjustmentPolicy
from app.market_data.calendar import CalendarPolicy
from app.market_data.contracts import (
    AssetType,
    EligibilitySource,
    Frequency,
    InstrumentIdentifier,
    MarketDataRequest,
    ProviderCapabilities,
)
from app.market_data.panel import MarketDataPanel
from app.market_data.providers.protocol import MarketDataProvider, RawProviderDataset
from app.market_data.quality import DataQualityReport, validate_panel_quality

GENERATOR_VERSION = "synthetic-gen/1.0"


def _business_days(start: date, count: int) -> tuple[date, ...]:
    """Deterministic weekday-only calendar (Mon-Fri, no exchange holidays)."""
    out: list[date] = []
    d = start
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d)
        d = d + timedelta(days=1)
    return tuple(out)


def _series(symbol: str, seed: int, T: int) -> np.ndarray:
    """Deterministic synthetic price series with mid-sample reversal."""
    h = int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed ^ h)
    drift = 0.0002 + (h % 7) * 0.00005
    vol = 0.008 + (h % 5) * 0.002
    base = 50.0 + (h % 150)
    prices = np.empty(T, dtype=float)
    p = base
    for t in range(T):
        d = drift
        if 220 <= t < 300:
            d = -drift * 1.5
        p *= 1.0 + d + rng.normal(0.0, vol)
        prices[t] = max(p, 1.0)
    return prices


class SyntheticFixtureProvider(MarketDataProvider):
    """Deterministic synthetic fixture. Requires explicit opt-in."""

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_name="synthetic_fixture",
            provider_version=GENERATOR_VERSION,
            supported_frequencies=(Frequency.DAILY,),
            supported_asset_types=(AssetType.EQUITY, AssetType.ETF),
            supported_fields=("open", "high", "low", "close", "volume"),
            supported_adjustment_policies=(AdjustmentPolicy.RAW,),
            timestamps_represent="bar_close",
            timezone_behavior="utc_normalized",
            point_in_time_safe=False,
            historical_revisions_possible=False,
            delisted_instruments_supported=False,
            volume_available=True,
            benchmark_supported=True,
            max_instruments_per_request=100,
            max_date_range_days=3650,
        )

    def fetch(self, request: MarketDataRequest) -> RawProviderDataset:
        if not request.allow_synthetic_fixture:
            from app.market_data.errors import ProviderCapabilityError

            raise ProviderCapabilityError(
                "synthetic_fixture provider requires allow_synthetic_fixture=True"
            )
        return RawProviderDataset(
            provider="synthetic_fixture",
            provider_version=GENERATOR_VERSION,
            request=request,
            payload={"seed": request.extra.get("seed", 20240101)},
            retrieval_timestamp=datetime.now(UTC).isoformat(),
            warnings=(
                "synthetic fixture: weekday-only calendar, no exchange realism, deterministic seed",
            ),
        )

    def normalize(self, raw: RawProviderDataset) -> tuple[MarketDataPanel, DataQualityReport]:
        request = raw.request
        seed = raw.payload["seed"]
        T = 504  # ~2 years of weekday bars
        dates = _business_days(date(2021, 1, 4), T)

        # Build tradable assets
        tradable = [i for i in request.instruments]
        bench = request.benchmark
        bench_sym = bench.symbol if bench else "SPY"

        n_assets = len(tradable)
        close = np.zeros((T, n_assets))
        for j, inst in enumerate(tradable):
            close[:, j] = _series(inst.symbol, seed, T)

        # Valid OHLCV
        open_ = np.vstack([close[0], close[:-1]])
        high = np.maximum(open_, close) * 1.005
        low = np.minimum(open_, close) * 0.995
        volume = np.full((T, n_assets), 1_000_000.0)

        # Benchmark series
        bench_series = _series(bench_sym, seed, T) if bench else None

        # Masks: all eligible, all observed, none imputed
        eligibility = np.ones((T, n_assets), dtype=bool)
        observed = np.ones((T, n_assets), dtype=bool)
        imputed = np.zeros((T, n_assets), dtype=bool)

        panel = MarketDataPanel(
            dates=dates,
            instruments=tuple(tradable),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            benchmark_close=bench_series,
            benchmark_instrument=bench,
            eligibility_mask=eligibility,
            observed_mask=observed,
            imputation_mask=imputed,
            provider="synthetic_fixture",
            provider_version=GENERATOR_VERSION,
            retrieval_timestamp=datetime.now(UTC),
            as_of=request.as_of,
            calendar_policy=CalendarPolicy.SYNTHETIC_WEEKDAY,
            adjustment_policy=AdjustmentPolicy.RAW,
            missing_data_policy="none",
            eligibility_source=EligibilitySource.SYNTHETIC_FIXTURE,
            source_metadata={
                "generator_version": GENERATOR_VERSION,
                "seed": seed,
                "limitations": [
                    "synthetic weekday calendar",
                    "no exchange realism",
                    "deterministic seed",
                    "not point-in-time",
                ],
            },
        )
        quality = validate_panel_quality(panel, required_symbols=tuple(i.symbol for i in request.instruments))
        return panel, quality


__all__ = ["SyntheticFixtureProvider"]
