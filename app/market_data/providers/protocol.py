"""Provider protocol and capability declaration (Phase 3).

Every market-data provider must implement this protocol and declare its
capabilities. The registry rejects requests that exceed declared capabilities.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.market_data.contracts import MarketDataRequest, ProviderCapabilities
from app.market_data.panel import MarketDataPanel
from app.market_data.quality import DataQualityReport


@dataclass(frozen=True)
class RawProviderDataset:
    """Raw, unnormalized provider response. Never crosses into strategy execution."""

    provider: str
    provider_version: str
    request: MarketDataRequest
    payload: Any  # provider-specific (e.g., pandas DataFrame)
    retrieval_timestamp: str
    warnings: tuple[str, ...] = ()


class MarketDataProvider(ABC):
    """Protocol for canonical market-data providers."""

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """Declare what this provider can deliver."""
        ...

    @abstractmethod
    def fetch(self, request: MarketDataRequest) -> RawProviderDataset:
        """Retrieve raw data. May raise MarketDataError on failure."""
        ...

    @abstractmethod
    def normalize(self, raw: RawProviderDataset) -> tuple[MarketDataPanel, DataQualityReport]:
        """Normalize raw data to canonical panel + quality report."""
        ...

    def validate_request(self, request: MarketDataRequest) -> None:
        """Reject requests exceeding declared capabilities."""
        from app.market_data.errors import ProviderCapabilityError

        caps = self.capabilities()
        if request.frequency not in caps.supported_frequencies:
            raise ProviderCapabilityError(
                f"provider {caps.provider_name} does not support frequency {request.frequency}"
            )
        if request.adjustment_policy not in caps.supported_adjustment_policies:
            raise ProviderCapabilityError(
                f"provider {caps.provider_name} does not support adjustment {request.adjustment_policy}"
            )
        for field in request.required_fields:
            if field not in caps.supported_fields:
                raise ProviderCapabilityError(
                    f"provider {caps.provider_name} does not support field {field}"
                )
        if len(request.instruments) > caps.max_instruments_per_request:
            raise ProviderCapabilityError(
                f"provider {caps.provider_name} supports max {caps.max_instruments_per_request} instruments"
            )
        if request.benchmark and not caps.benchmark_supported:
            raise ProviderCapabilityError(
                f"provider {caps.provider_name} does not support benchmark data"
            )


__all__ = ["MarketDataProvider", "RawProviderDataset"]
