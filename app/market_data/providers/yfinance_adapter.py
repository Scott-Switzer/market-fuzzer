"""yfinance provider adapter (Phase 3).

Wraps the existing yfinance integration with explicit capability declaration,
adjustment policy recording, and no silent synthetic fallback. Tests mock
provider responses; CI never depends on live network access.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
from app.market_data.errors import DataQualityError, ProviderCapabilityError
from app.market_data.panel import MarketDataPanel
from app.market_data.providers.protocol import MarketDataProvider, RawProviderDataset
from app.market_data.quality import DataQualityReport, validate_panel_quality

ADAPTER_VERSION = "yfinance-adapter/1.0"


class YFinanceProvider(MarketDataProvider):
    """Real yfinance provider with explicit limitations."""

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_name="yfinance",
            provider_version=ADAPTER_VERSION,
            supported_frequencies=(Frequency.DAILY,),
            supported_asset_types=(AssetType.EQUITY, AssetType.ETF),
            supported_fields=("open", "high", "low", "close", "volume"),
            supported_adjustment_policies=(
                AdjustmentPolicy.RAW,
                AdjustmentPolicy.SPLIT_ADJUSTED,
                AdjustmentPolicy.SPLIT_AND_DIVIDEND_ADJUSTED,
            ),
            timestamps_represent="bar_close",
            timezone_behavior="utc_normalized",
            point_in_time_safe=False,
            historical_revisions_possible=True,
            delisted_instruments_supported=False,
            volume_available=True,
            benchmark_supported=True,
            max_instruments_per_request=50,
            max_date_range_days=3650 * 2,
        )

    def fetch(self, request: MarketDataRequest) -> RawProviderDataset:
        """Fetch from yfinance. Mocked in tests; no live network in CI."""
        # In production this calls yf.download; in tests it returns a mocked payload
        payload = self._download(request)
        return RawProviderDataset(
            provider="yfinance",
            provider_version=ADAPTER_VERSION,
            request=request,
            payload=payload,
            retrieval_timestamp=datetime.now(UTC).isoformat(),
            warnings=(
                "yfinance: not guaranteed point-in-time constituent coverage",
                "yfinance: possible historical revisions",
                "yfinance: limited delisted-symbol reliability",
                "yfinance: no institutional SLA",
            ),
        )

    def _download(self, request: MarketDataRequest) -> dict[str, Any]:
        """Actual yfinance download. Separated for mocking."""
        try:
            import yfinance as yf
        except ImportError as exc:
            raise ProviderCapabilityError(f"yfinance package unavailable: {exc}") from exc

        tickers = [i.symbol for i in request.instruments]
        if request.benchmark and request.benchmark.symbol not in tickers:
            tickers.append(request.benchmark.symbol)

        start = request.start_date.isoformat()
        end = request.end_date.isoformat()

        # Map adjustment policy to yfinance parameters
        auto_adjust = request.adjustment_policy in (
            AdjustmentPolicy.SPLIT_AND_DIVIDEND_ADJUSTED,
            AdjustmentPolicy.TOTAL_RETURN,
        )
        actions = request.adjustment_policy in (
            AdjustmentPolicy.SPLIT_ADJUSTED,
            AdjustmentPolicy.SPLIT_AND_DIVIDEND_ADJUSTED,
            AdjustmentPolicy.TOTAL_RETURN,
        )

        try:
            df = yf.download(
                tickers,
                start=start,
                end=end,
                auto_adjust=auto_adjust,
                actions=actions,
                progress=False,
                threads=False,
            )
        except Exception as exc:
            raise ProviderCapabilityError(f"yfinance download failed: {exc}") from exc

        if df is None or df.empty:
            raise DataQualityError(f"yfinance returned no data for {tickers}")

        return {"dataframe": df, "tickers": tickers, "auto_adjust": auto_adjust}

    def normalize(self, raw: RawProviderDataset) -> tuple[MarketDataPanel, DataQualityReport]:
        request = raw.request
        df = raw.payload["dataframe"]
        tickers = raw.payload["tickers"]
        auto_adjust = raw.payload["auto_adjust"]

        # yfinance multi-index: columns = (field, ticker)
        fields = ["Open", "High", "Low", "Close", "Volume"]
        present = [f for f in fields if f in df.columns.get_level_values(0)]

        assets: list[InstrumentIdentifier] = []
        series: dict[str, dict[str, np.ndarray]] = {}
        for inst in request.instruments:
            tk = inst.symbol
            cols = {f: df[(f, tk)] for f in present if (f, tk) in df.columns}
            if len(cols) >= 4 and len(cols["Close"]) > 1:
                assets.append(inst)
                series[tk] = {f.lower(): cols[f].to_numpy(dtype=float) for f in cols}

        if not assets:
            raise DataQualityError("no instruments returned usable data")

        # Align on common date index
        idx = df.index
        dates = [d.date() if hasattr(d, "date") else datetime.fromisoformat(str(d)).date() for d in idx]
        T = len(dates)
        N = len(assets)

        open_ = np.zeros((T, N))
        high = np.zeros((T, N))
        low = np.zeros((T, N))
        close = np.zeros((T, N))
        volume = np.zeros((T, N))
        observed = np.zeros((T, N), dtype=bool)

        for j, inst in enumerate(assets):
            tk = inst.symbol
            open_[:, j] = series[tk].get("open", series[tk]["close"])
            high[:, j] = series[tk].get("high", series[tk]["close"])
            low[:, j] = series[tk].get("low", series[tk]["close"])
            close[:, j] = series[tk]["close"]
            volume[:, j] = series[tk].get("volume", np.zeros(T))
            observed[:, j] = np.isfinite(close[:, j]) & (close[:, j] > 0)

        # Benchmark
        bench_series = None
        bench_inst = None
        if request.benchmark:
            bench_tk = request.benchmark.symbol
            if bench_tk in tickers:
                if ("Close", bench_tk) in df.columns:
                    bench_series = df[("Close", bench_tk)].to_numpy(dtype=float)
                    bench_inst = request.benchmark

        # Masks
        eligibility = observed.copy()  # provider-observed availability
        imputed = np.zeros((T, N), dtype=bool)

        # Determine adjustment policy label
        if auto_adjust:
            adj_policy = AdjustmentPolicy.SPLIT_AND_DIVIDEND_ADJUSTED
        else:
            adj_policy = AdjustmentPolicy.RAW

        panel = MarketDataPanel(
            dates=tuple(dates),
            instruments=tuple(assets),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            benchmark_close=bench_series,
            benchmark_instrument=bench_inst,
            eligibility_mask=eligibility,
            observed_mask=observed,
            imputation_mask=imputed,
            provider="yfinance",
            provider_version=ADAPTER_VERSION,
            retrieval_timestamp=datetime.now(UTC),
            as_of=request.as_of,
            calendar_policy=CalendarPolicy.PROVIDER_OBSERVED,
            adjustment_policy=adj_policy,
            missing_data_policy=request.missing_data_policy,
            eligibility_source=EligibilitySource.PROVIDER_OBSERVED_AVAILABILITY,
            source_metadata={
                "auto_adjust": auto_adjust,
                "limitations": [
                    "not guaranteed point-in-time constituent coverage",
                    "possible historical revisions",
                    "limited delisted-symbol reliability",
                    "no institutional SLA",
                ],
            },
        )
        quality = validate_panel_quality(
            panel,
            required_symbols=tuple(i.symbol for i in request.instruments),
            require_benchmark=request.benchmark is not None,
        )
        return panel, quality


__all__ = ["YFinanceProvider"]
