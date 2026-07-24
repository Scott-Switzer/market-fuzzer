"""Normalized market-data model for the Fenrix submission backtester.

A single, strict ``MarketDataPanel`` is the contract between data adapters
(yfinance / Fenrix / deterministic fixture) and the portfolio engine. It is a
genuine T x N panel: every asset has its own price path, dates are real
calendar dates (never stringified integer indices), and shape mismatches raise
instead of being silently resized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np


@dataclass(frozen=True)
class AssetMetadata:
    ticker: str
    sector: str | None = None
    asset_class: str = "equity"
    is_benchmark: bool = False
    point_in_time: bool = True


@dataclass(frozen=True)
class DataProvenance:
    source: str  # "yfinance" | "fenrix" | "deterministic_fixture"
    tier: int  # 1 Fenrix, 2 yfinance, 3 fixture
    retrieval_timestamp: str | None = None
    source_hash: str | None = None
    transformations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    label: str = ""  # human-facing badge, e.g. "yfinance (research/educational)"

    def __post_init__(self) -> None:
        if not self.label:
            object.__setattr__(self, "label", f"{self.source} (tier {self.tier})")


@dataclass(frozen=True)
class MarketDataPanel:
    dates: tuple[date, ...]
    assets: tuple[str, ...]
    open: np.ndarray  # T x N
    high: np.ndarray  # T x N
    low: np.ndarray  # T x N
    close: np.ndarray  # T x N
    volume: np.ndarray  # T x N
    benchmark_close: np.ndarray | None
    metadata: dict[str, AssetMetadata]
    provenance: DataProvenance

    def __post_init__(self) -> None:
        self._validate()

    # --- strict validation -------------------------------------------------
    def _validate(self) -> None:
        T = len(self.dates)
        N = len(self.assets)
        for name in ("open", "high", "low", "close", "volume"):
            arr = getattr(self, name)
            if arr.shape != (T, N):
                raise ValueError(f"MarketDataPanel.{name} shape {arr.shape} != required (T={T}, N={N})")
        # monotonic unique dates
        if len({d for d in self.dates}) != T:
            raise ValueError("MarketDataPanel dates are not unique")
        for i in range(1, T):
            if self.dates[i] <= self.dates[i - 1]:
                raise ValueError(f"MarketDataPanel dates not strictly increasing at index {i}")
        if len(set(self.assets)) != N:
            raise ValueError("MarketDataPanel assets are not unique")
        # finite positive prices
        for name in ("open", "high", "low", "close"):
            arr = getattr(self, name)
            if np.any(arr <= 0) or not np.all(np.isfinite(arr)):
                raise ValueError(f"MarketDataPanel.{name} must be finite and positive")
        # high >= low, high >= close >= low style consistency
        if np.any(self.high < self.low):
            raise ValueError("MarketDataPanel high < low somewhere")
        # benchmark alignment
        if self.benchmark_close is not None and self.benchmark_close.shape != (T,):
            raise ValueError("MarketDataPanel.benchmark_close must be 1-D length T")
        # metadata covers assets
        missing = [a for a in self.assets if a not in self.metadata]
        if missing:
            raise ValueError(f"MarketDataPanel missing metadata for: {missing}")

    # --- convenience --------------------------------------------------------
    @property
    def T(self) -> int:
        return len(self.dates)

    @property
    def N(self) -> int:
        return len(self.assets)

    def asset_index(self, ticker: str) -> int:
        return self.assets.index(ticker)

    def simple_returns(self, prices: np.ndarray | None = None) -> np.ndarray:
        """Close-to-close returns for each asset (T-1 x N)."""
        px = self.close if prices is None else prices
        return px[1:] / px[:-1] - 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "dates": [d.isoformat() for d in self.dates],
            "assets": list(self.assets),
            "open": self.open.tolist(),
            "high": self.high.tolist(),
            "low": self.low.tolist(),
            "close": self.close.tolist(),
            "volume": self.volume.tolist(),
            "benchmark_close": None if self.benchmark_close is None else self.benchmark_close.tolist(),
            "metadata": {k: v.__dict__ for k, v in self.metadata.items()},
            "provenance": self.provenance.__dict__,
        }
