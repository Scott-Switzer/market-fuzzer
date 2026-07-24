"""Flagship cross-sectional momentum/volatility strategy specification.

This is a real structured strategy (not a hard-coded label). It is compiled into
the canonical ``Strategy`` DSL so the same approved hash flows through historical
backtest -> sealed campaign -> replay -> evidence export.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Fixed, clearly-named liquid-equity demo universe (NOT "the S&P 500").
# SPY is the benchmark, not a tradable member of the long/short book here, but
# we keep it in the panel for benchmark accounting and allow it as a tradable
# candidate if the universe config chooses. The default flags benchmark separately.
DEMO_UNIVERSE: list[str] = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
    "BRK-B",
    "JPM",
    "V",
    "UNH",
    "XOM",
    "JNJ",
    "WMT",
    "MA",
    "PG",
    "HD",
    "CVX",
    "KO",
    "PEP",
    "COST",
    "ABBV",
    "AVGO",
    "MRK",
    "PFE",
    "T",
    "BAC",
    "DIS",
    "CSCO",
    "ADBE",
]

BENCHMARK = "SPY"

FIXED_START = "2018-01-01"
FIXED_END = "2025-12-31"


@dataclass(frozen=True)
class CrossSectionalSpec:
    """Defensible default specification (do NOT tune to look good)."""

    universe: list[str] = field(default_factory=lambda: list(DEMO_UNIVERSE))
    benchmark: str = BENCHMARK
    start: str = FIXED_START
    end: str = FIXED_END
    signal_frequency: str = "monthly"
    rebalance_frequency: str = "monthly"
    # feature formulas (documentation + identity; engine implements the math)
    momentum_lookback: int = 252
    momentum_short: int = 21
    volatility_window: int = 63
    momentum_weight: float = 0.75
    low_volatility_weight: float = 0.25
    long_quantile: float = 0.20
    short_quantile: float = 0.20
    gross_exposure: float = 1.0
    net_exposure: float = 0.0
    weighting: str = "equal"
    max_position_weight: float = 0.10
    decision_time: str = "close"
    fill_time: str = "next_open"
    commission_bps: float = 5.0
    spread_bps: float = 2.0
    slippage_bps: float = 3.0
    borrow_bps: float = 50.0  # annualized cost of short financing
    initial_capital: float = 1_000_000.0

    def to_feature_dict(self) -> dict[str, Any]:
        return {
            "momentum_12_1": {
                "id": "momentum_12_1",
                "formula": f"close[t-{self.momentum_short}]/close[t-{self.momentum_lookback}] - 1",
            },
            "volatility_63d": {
                "id": "volatility_63d",
                "formula": f"std(daily_returns[t-{self.volatility_window}:t]) * sqrt(252)",
            },
        }

    def to_clause_ledger_fragment(self) -> list[dict[str, Any]]:
        return [
            {"id": "momentum_12_1", "kind": "feature", "source": "price", "field": "close"},
            {"id": "volatility_63d", "kind": "feature", "source": "return", "field": "close"},
            {
                "id": "composite",
                "kind": "composite",
                "momentum_weight": self.momentum_weight,
                "low_volatility_weight": self.low_volatility_weight,
            },
            {"id": "long_quantile", "kind": "selection", "quantile": self.long_quantile},
            {"id": "short_quantile", "kind": "selection", "quantile": self.short_quantile},
            {
                "id": "weights",
                "kind": "weighting",
                "method": self.weighting,
                "max_position_weight": self.max_position_weight,
            },
            {"id": "exposure", "kind": "exposure", "gross": self.gross_exposure, "net": self.net_exposure},
            {
                "id": "execution",
                "kind": "execution",
                "decision_time": self.decision_time,
                "fill_time": self.fill_time,
                "commission_bps": self.commission_bps,
                "spread_bps": self.spread_bps,
                "slippage_bps": self.slippage_bps,
            },
        ]
