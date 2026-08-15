"""Provider registry (Phase 3).

No provider-name if/else chains in production services. All providers are
registered here and looked up by name.
"""

from __future__ import annotations

from app.market_data.contracts import MarketDataRequest
from app.market_data.errors import ProviderNotFoundError
from app.market_data.panel import MarketDataPanel
from app.market_data.providers.protocol import MarketDataProvider, RawProviderDataset
from app.market_data.quality import DataQualityReport


class ProviderRegistry:
    """Registry of available market-data providers."""

    def __init__(self) -> None:
        self._providers: dict[str, MarketDataProvider] = {}

    def register(self, provider: MarketDataProvider) -> None:
        caps = provider.capabilities()
        self._providers[caps.provider_name] = provider

    def get(self, name: str) -> MarketDataProvider:
        if name not in self._providers:
            raise ProviderNotFoundError(f"unknown market-data provider: {name}")
        return self._providers[name]

    def list_providers(self) -> list[str]:
        return sorted(self._providers.keys())

    def acquire(self, request: MarketDataRequest) -> tuple[MarketDataPanel, DataQualityReport]:
        """Validate, fetch, normalize, and return canonical panel + quality."""
        provider = self.get(request.provider_name)
        provider.validate_request(request)
        raw = provider.fetch(request)
        return provider.normalize(raw)


_default_registry = ProviderRegistry()


def default_registry() -> ProviderRegistry:
    return _default_registry


__all__ = ["ProviderRegistry", "default_registry"]
