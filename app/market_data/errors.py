"""Canonical market-data error taxonomy (Phase 3).

Every error is a ``MarketDataError`` so the V2 router can map it to a correct
HTTP status (422 for invalid request / semantic incompatibility, 404 for missing
dataset, 409 for digest mismatch, 500 for genuine provider failure).
"""

from __future__ import annotations


class MarketDataError(Exception):
    """Base class for all canonical market-data errors (fail-closed)."""


class ProviderNotFoundError(MarketDataError):
    """Requested provider is not registered."""


class ProviderCapabilityError(MarketDataError):
    """Request exceeds the provider's declared capabilities."""


class UnsupportedAdjustmentError(MarketDataError):
    """Requested adjustment policy is not supported by the provider."""


class CalendarPolicyError(MarketDataError):
    """Requested calendar policy is invalid or unsupported."""


class MissingDataError(MarketDataError):
    """Missing-data policy violation (e.g., required gap, fill across inception)."""


class EligibilityError(MarketDataError):
    """Point-in-time eligibility violation (e.g., current membership labeled PIT,
    as_of violation, eligibility before inception)."""


class DataQualityError(MarketDataError):
    """Data quality check failed (fatal, not warning)."""


class DatasetDigestMismatchError(MarketDataError):
    """Stored dataset digest does not match recomputed digest."""


class DatasetNotFoundError(MarketDataError):
    """Requested frozen dataset does not exist."""


__all__ = [
    "CalendarPolicyError",
    "DataQualityError",
    "DatasetDigestMismatchError",
    "DatasetNotFoundError",
    "EligibilityError",
    "MarketDataError",
    "MissingDataError",
    "ProviderCapabilityError",
    "ProviderNotFoundError",
    "UnsupportedAdjustmentError",
]
