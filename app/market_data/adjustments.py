"""Explicit price-adjustment policies (Phase 3).

Never call yfinance ``auto_adjust=True`` output "raw".
"""

from __future__ import annotations

from enum import StrEnum


class AdjustmentPolicy(StrEnum):
    """How prices were adjusted for corporate actions."""

    RAW = "raw"
    """Unadjusted prices as traded (splits and dividends not applied)."""

    SPLIT_ADJUSTED = "split_adjusted"
    """Split-adjusted only; dividends not reinvested."""

    SPLIT_AND_DIVIDEND_ADJUSTED = "split_and_dividend_adjusted"
    """Split- and dividend-adjusted (total-return approximation)."""

    TOTAL_RETURN = "total_return"
    """Fully adjusted total-return series (only if genuinely supported)."""


__all__ = ["AdjustmentPolicy"]
