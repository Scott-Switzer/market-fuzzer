from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class HistoricalDataContract:
    freq: str
    start: str
    end: str
    fields: Sequence[str] = ("close", "high", "low", "open", "volume")
    corporate_actions: bool = True
    point_in_time_universe: bool = True
    provenance: dict[str, Any] = field(default_factory=dict)
    assets: list[str] | None = None
    lookback_bars: int | None = None
    source: str | None = None
    benchmark_symbol: str | None = None
    survivorship_bias_risk: bool = False
