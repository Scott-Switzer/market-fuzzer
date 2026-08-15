"""Immutable market-data contracts (Phase 3).

Every data request is a typed, validated ``MarketDataRequest``. Every response
is a canonical ``MarketDataPanel`` with full provenance. No raw provider
dataframes cross into strategy execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from app.market_data.adjustments import AdjustmentPolicy
from app.market_data.calendar import CalendarPolicy


class AssetType(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    INDEX = "index"
    CRYPTO = "crypto"
    FX = "fx"


class Frequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class EligibilitySource(StrEnum):
    """How the tradable universe was determined."""

    STATIC_DECLARED_UNIVERSE = "static_declared_universe"
    PROVIDER_OBSERVED_AVAILABILITY = "provider_observed_availability"
    POINT_IN_TIME_MEMBERSHIP = "point_in_time_membership"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


@dataclass(frozen=True)
class InstrumentIdentifier:
    """Stable, provider-agnostic instrument identity.

    Ticker text alone is NOT globally unique; this record disambiguates.
    """

    symbol: str
    venue: str | None = None
    asset_type: AssetType = AssetType.EQUITY
    currency: str = "USD"
    provider_symbol: str | None = None
    stable_id: str | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("InstrumentIdentifier.symbol must be non-empty")
        if self.provider_symbol is None:
            object.__setattr__(self, "provider_symbol", self.symbol)
        if self.stable_id is None:
            # Stable ID = symbol + venue (or provider if no venue)
            base = f"{self.symbol}:{self.venue or 'default'}"
            import hashlib

            object.__setattr__(self, "stable_id", hashlib.sha256(base.encode()).hexdigest()[:16])


@dataclass(frozen=True)
class MarketDataRequest:
    """Complete, immutable specification of a data request.

    ``as_of`` is the latest information timestamp the request is allowed to use.
    ``allow_synthetic_fixture`` must be explicitly True for synthetic data.
    """

    instruments: tuple[InstrumentIdentifier, ...]
    start_date: date
    end_date: date
    frequency: Frequency = Frequency.DAILY
    required_fields: tuple[str, ...] = ("open", "high", "low", "close", "volume")
    calendar_policy: CalendarPolicy = CalendarPolicy.PROVIDER_OBSERVED
    adjustment_policy: AdjustmentPolicy = AdjustmentPolicy.RAW
    missing_data_policy: str = "reject"  # "reject" | "preserve" | "limited_forward_fill"
    benchmark: InstrumentIdentifier | None = None
    benchmark_tradable: bool = False
    eligibility_source: EligibilitySource = EligibilitySource.STATIC_DECLARED_UNIVERSE
    as_of: datetime | None = None
    provider_name: str = "synthetic_fixture"
    provider_config_version: str = "1.0"
    allow_synthetic_fixture: bool = False
    max_forward_fill_gap: int = 0  # only for limited_forward_fill
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.instruments:
            raise ValueError("MarketDataRequest.instruments must be non-empty")
        if self.start_date > self.end_date:
            raise ValueError("MarketDataRequest.start_date must be <= end_date")
        # Note: benchmark-in-universe validation is handled by the backtest service
        # (BenchmarkConflictError), not here, to allow the service to return 422.
        if self.as_of is not None and self.as_of.date() > self.end_date:
            # as_of must not be after the requested end date (we want data up to end_date)
            pass  # as_of is about information timestamp, not data range
        if self.missing_data_policy == "limited_forward_fill" and self.max_forward_fill_gap < 1:
            raise ValueError("limited_forward_fill requires max_forward_fill_gap >= 1")


@dataclass(frozen=True)
class HistoryRequirements:
    """Executor-derived minimum history contract (consolidated from Phase 2.6)."""

    min_total_bars: int
    min_contiguous_bars: int
    required_fields: tuple[str, ...] = ("close",)
    required_frequency: Frequency = Frequency.DAILY
    per_asset: bool = True
    benchmark_required: bool = False
    warmup_bars: int = 0
    missing_bars_invalidate: bool = True
    signal_reason: str = ""


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider can actually deliver."""

    provider_name: str
    provider_version: str
    supported_frequencies: tuple[Frequency, ...]
    supported_asset_types: tuple[AssetType, ...]
    supported_fields: tuple[str, ...]
    supported_adjustment_policies: tuple[AdjustmentPolicy, ...]
    timestamps_represent: str = "bar_close"  # "bar_open" | "bar_close"
    timezone_behavior: str = "utc_normalized"  # "utc_normalized" | "provider_local"
    point_in_time_safe: bool = False
    historical_revisions_possible: bool = True
    delisted_instruments_supported: bool = False
    volume_available: bool = True
    benchmark_supported: bool = True
    max_instruments_per_request: int = 100
    max_date_range_days: int = 3650


__all__ = [
    "AssetType",
    "EligibilitySource",
    "Frequency",
    "HistoryRequirements",
    "InstrumentIdentifier",
    "MarketDataRequest",
    "ProviderCapabilities",
]
