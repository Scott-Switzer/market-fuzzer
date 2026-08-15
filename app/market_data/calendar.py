"""Explicit trading-calendar policies (Phase 3).

No synthetic weekday calendar may be presented as an actual exchange calendar.
"""

from __future__ import annotations

from enum import StrEnum


class CalendarPolicy(StrEnum):
    """How the panel's date index was constructed."""

    PROVIDER_OBSERVED = "provider_observed"
    """Dates are whatever the provider returned (e.g., yfinance NYSE dates)."""

    NAMED_EXCHANGE = "named_exchange"
    """Dates follow a named exchange calendar (e.g., NYSE, LSE) with holidays."""

    SYNTHETIC_WEEKDAY = "synthetic_weekday"
    """Weekday-only (Mon-Fri) fixture calendar; exchange holidays NOT modeled.
    Must never be presented as an actual exchange calendar."""


__all__ = ["CalendarPolicy"]
