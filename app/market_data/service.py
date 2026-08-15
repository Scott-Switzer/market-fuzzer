"""Canonical market-data service (Phase 3).

Single entry point for all market-data acquisition in the V2 product path.
Converts legacy data-source dicts to canonical requests, acquires panels
through the provider registry, and returns panels with full provenance.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.market_data.contracts import (
    EligibilitySource,
    Frequency,
    InstrumentIdentifier,
    MarketDataRequest,
)
from app.market_data.panel import MarketDataPanel
from app.market_data.quality import DataQualityReport
from app.market_data.registry import default_registry


def _make_instrument(symbol: str, asset_type: str = "equity") -> InstrumentIdentifier:
    from app.market_data.contracts import AssetType

    return InstrumentIdentifier(
        symbol=symbol,
        asset_type=AssetType(asset_type),
        currency="USD",
    )


def request_from_legacy(
    data_source: dict[str, Any],
    universe: list[str],
    benchmark: str | None,
    benchmark_tradable: bool = False,
    allow_synthetic: bool = False,
) -> MarketDataRequest:
    """Convert a legacy V2 data_source dict to a canonical request."""
    source = data_source.get("source", "demo_fixture")

    instruments = tuple(_make_instrument(s) for s in universe)
    bench_inst = _make_instrument(benchmark) if benchmark else None

    # Map legacy source to provider + eligibility
    if source == "demo_fixture":
        provider = "synthetic_fixture"
        eligibility = EligibilitySource.SYNTHETIC_FIXTURE
        allow_syn = True
    elif source == "yfinance":
        provider = "yfinance"
        eligibility = EligibilitySource.PROVIDER_OBSERVED_AVAILABILITY
        allow_syn = allow_synthetic
    elif source == "uploaded_csv":
        provider = "uploaded_csv"
        eligibility = EligibilitySource.STATIC_DECLARED_UNIVERSE
        allow_syn = allow_synthetic
    else:
        # Pass through unknown sources to the registry (will raise ProviderNotFoundError)
        provider = source
        eligibility = EligibilitySource.STATIC_DECLARED_UNIVERSE
        allow_syn = allow_synthetic

    start = data_source.get("start")
    end = data_source.get("end")
    start_date = date.fromisoformat(start) if start else date(2021, 1, 4)
    end_date = date.fromisoformat(end) if end else date(2023, 12, 31)

    seed = data_source.get("seed")
    extra = {"seed": seed} if seed else {}

    from app.market_data.adjustments import AdjustmentPolicy
    from app.market_data.calendar import CalendarPolicy

    cal_policy = data_source.get("calendar_policy", "provider_observed")
    if isinstance(cal_policy, str):
        cal_policy = CalendarPolicy(cal_policy)

    adj_policy = data_source.get("adjustment_policy", "raw")
    if isinstance(adj_policy, str):
        adj_policy = AdjustmentPolicy(adj_policy)

    return MarketDataRequest(
        instruments=instruments,
        start_date=start_date,
        end_date=end_date,
        frequency=Frequency.DAILY,
        required_fields=("open", "high", "low", "close", "volume"),
        calendar_policy=cal_policy,
        adjustment_policy=adj_policy,
        missing_data_policy=data_source.get("missing_data_policy", "reject"),
        benchmark=bench_inst,
        benchmark_tradable=benchmark_tradable,
        eligibility_source=eligibility,
        as_of=data_source.get("as_of"),
        provider_name=provider,
        provider_config_version=data_source.get("provider_config_version", "1.0"),
        allow_synthetic_fixture=allow_syn,
        max_forward_fill_gap=data_source.get("max_forward_fill_gap", 0),
        extra=extra,
    )


def acquire_panel(
    data_source: dict[str, Any],
    universe: list[str],
    benchmark: str | None,
    benchmark_tradable: bool = False,
    allow_synthetic: bool = False,
) -> tuple[MarketDataPanel, DataQualityReport]:
    """Acquire a canonical panel through the provider registry.

    Raises MarketDataError subclasses on failure (never silent fallback).
    """
    request = request_from_legacy(data_source, universe, benchmark, benchmark_tradable, allow_synthetic)
    registry = default_registry()
    return registry.acquire(request)


def acquire_panel_from_request(request: MarketDataRequest) -> tuple[MarketDataPanel, DataQualityReport]:
    """Acquire a panel from a canonical request directly."""
    registry = default_registry()
    return registry.acquire(request)


__all__ = [
    "acquire_panel",
    "acquire_panel_from_request",
    "request_from_legacy",
]
