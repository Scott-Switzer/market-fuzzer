"""Data-source and point-in-time provenance contracts (reset brief section 12 data policy)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DataTier(StrEnum):
    FENRIX_BUNDLE = "fenrix_bundle"  # tier 1 -- anonymized, RELATIVE dates
    YFINANCE = "yfinance"  # tier 2 -- real calendar, cached
    SYNTHETIC_FIXTURE = "synthetic_fixture"  # tier 3 -- deterministic, offline/CI


class DataProvenance(BaseModel):
    """Attached to every market-data panel. The UI/evidence must surface this so
    a synthetic panel is never presented as historical (integrity gate 5) and a
    relative-date bundle is never presented as point-in-time historical."""

    model_config = ConfigDict(extra="forbid")

    source: str
    tier: DataTier
    label: str
    dates_are_relative: bool = False  # tier-1 DAY_0000 labels, not a calendar
    point_in_time: bool = False  # True only when effective_at timestamps exist
    source_hash: str = ""
    cache_key: str | None = None
    notes: list[str] = Field(default_factory=list)


class DataSourceRegistration(BaseModel):
    """Durable registration of a data source available to a project."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str
    tier: DataTier
    display_name: str
    universe_available: list[str] = Field(default_factory=list)
    benchmark_available: list[str] = Field(default_factory=list)
    calendar: str = "unknown"  # "nyse" | "relative" | "synthetic"
    point_in_time: bool = False


__all__ = ["DataTier", "DataProvenance", "DataSourceRegistration"]
