"""Canonical market-data layer (Phase 3).

Single authoritative package for all market data entering Fenrix strategy execution.
No provider-specific dataframes cross this boundary; only canonical ``MarketDataPanel``
objects with full provenance, eligibility masks, and dataset digests are accepted.
"""

from app.market_data.adjustments import AdjustmentPolicy
from app.market_data.calendar import CalendarPolicy
from app.market_data.contracts import (
    HistoryRequirements,
    InstrumentIdentifier,
    MarketDataRequest,
)
from app.market_data.digest import compute_dataset_digest
from app.market_data.errors import (
    CalendarPolicyError,
    DataQualityError,
    DatasetDigestMismatchError,
    EligibilityError,
    MarketDataError,
    MissingDataError,
    ProviderCapabilityError,
    ProviderNotFoundError,
    UnsupportedAdjustmentError,
)
from app.market_data.panel import MarketDataPanel
from app.market_data.quality import DataQualityReport, MissingDataPolicy

# Register providers
from app.market_data.providers.synthetic_fixture import SyntheticFixtureProvider
from app.market_data.providers.yfinance_adapter import YFinanceProvider
from app.market_data.registry import default_registry

_reg = default_registry()
_reg.register(SyntheticFixtureProvider())
_reg.register(YFinanceProvider())

__all__ = [
    "AdjustmentPolicy",
    "CalendarPolicy",
    "DataQualityError",
    "DataQualityReport",
    "DatasetDigestMismatchError",
    "EligibilityError",
    "HistoryRequirements",
    "InstrumentIdentifier",
    "MarketDataError",
    "MarketDataPanel",
    "MarketDataRequest",
    "MissingDataError",
    "MissingDataPolicy",
    "ProviderCapabilityError",
    "ProviderNotFoundError",
    "UnsupportedAdjustmentError",
    "CalendarPolicyError",
    "compute_dataset_digest",
]
