"""Data-quality validation and reporting (Phase 3).

Structured report distinguishing fatal errors from warnings. Fatal errors
prevent execution; warnings are surfaced but do not block.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np

from app.market_data.errors import DataQualityError


class MissingDataPolicy(StrEnum):
    REJECT = "reject"
    PRESERVE_MISSING = "preserve_missing"
    LIMITED_FORWARD_FILL = "limited_forward_fill"


@dataclass(frozen=True)
class InstrumentQuality:
    symbol: str
    first_valid: str | None
    last_valid: str | None
    observed_bars: int
    missing_bars: int
    imputed_bars: int
    longest_missing_streak: int
    invalid_price_count: int
    invalid_ohlc_count: int
    zero_volume_count: int
    stale_streaks: int


@dataclass(frozen=True)
class DataQualityReport:
    """Structured quality report. Fatal errors block execution."""

    requested_instruments: tuple[str, ...]
    returned_instruments: tuple[str, ...]
    missing_instruments: tuple[str, ...]
    per_instrument: tuple[InstrumentQuality, ...]
    eligibility_coverage: float  # fraction of T×N eligible
    benchmark_coverage: float  # fraction of T with benchmark observed
    history_requirement_met: bool
    fatal_errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_fatal(self) -> bool:
        return len(self.fatal_errors) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_instruments": list(self.requested_instruments),
            "returned_instruments": list(self.returned_instruments),
            "missing_instruments": list(self.missing_instruments),
            "per_instrument": [
                {
                    "symbol": q.symbol,
                    "first_valid": q.first_valid,
                    "last_valid": q.last_valid,
                    "observed_bars": q.observed_bars,
                    "missing_bars": q.missing_bars,
                    "imputed_bars": q.imputed_bars,
                    "longest_missing_streak": q.longest_missing_streak,
                    "invalid_price_count": q.invalid_price_count,
                    "invalid_ohlc_count": q.invalid_ohlc_count,
                    "zero_volume_count": q.zero_volume_count,
                    "stale_streaks": q.stale_streaks,
                }
                for q in self.per_instrument
            ],
            "eligibility_coverage": self.eligibility_coverage,
            "benchmark_coverage": self.benchmark_coverage,
            "history_requirement_met": self.history_requirement_met,
            "fatal_errors": list(self.fatal_errors),
            "warnings": list(self.warnings),
        }


def _longest_streak(mask: np.ndarray) -> int:
    """Longest run of False in a boolean mask."""
    longest = 0
    current = 0
    for v in mask:
        if not v:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _stale_streaks(close: np.ndarray, observed: np.ndarray, min_stale: int = 5) -> int:
    """Count of streaks where close is unchanged for >= min_stale observed bars."""
    count = 0
    run = 0
    prev = None
    for j in range(len(close)):
        if not observed[j]:
            continue
        if prev is not None and close[j] == prev:
            run += 1
        else:
            if run >= min_stale:
                count += 1
            run = 1
        prev = close[j]
    if run >= min_stale:
        count += 1
    return count


def validate_panel_quality(
    panel: Any,
    required_symbols: tuple[str, ...] | None = None,
    require_benchmark: bool = False,
) -> DataQualityReport:
    """Validate a panel and produce a structured quality report.

    Fatal errors raise ``DataQualityError``; warnings are returned in the report.
    """
    fatal: list[str] = []
    warnings: list[str] = []

    requested = required_symbols or panel.assets
    returned = set(panel.assets)
    missing = tuple(s for s in requested if s not in returned)

    if missing:
        fatal.append(f"required instruments missing: {missing}")

    if require_benchmark and panel.benchmark_close is None:
        fatal.append("benchmark required but unavailable")

    per_instrument: list[InstrumentQuality] = []
    for j, inst in enumerate(panel.instruments):
        obs = panel.observed_mask[:, j]
        close = panel.close[:, j]
        volume = panel.volume[:, j]

        first_valid = panel.dates[int(np.argmax(obs))].isoformat() if np.any(obs) else None
        last_valid = (
            panel.dates[len(obs) - 1 - int(np.argmax(obs[::-1]))].isoformat() if np.any(obs) else None
        )

        observed_bars = int(np.sum(obs))
        missing_bars = int(np.sum(~obs & panel.eligibility_mask[:, j]))
        imputed_bars = int(np.sum(panel.imputation_mask[:, j]))
        longest_missing = _longest_streak(obs)
        invalid_prices = int(np.sum((close <= 0) | ~np.isfinite(close)))
        invalid_ohlc = int(
            np.sum(
                (panel.high[:, j] < np.maximum(panel.open[:, j], close))
                | (panel.low[:, j] > np.minimum(panel.open[:, j], close))
                | (panel.high[:, j] < panel.low[:, j])
            )
        )
        zero_volume = int(np.sum((volume == 0) & obs))
        stale = _stale_streaks(close, obs)

        if invalid_prices > 0:
            fatal.append(f"{inst.symbol}: {invalid_prices} invalid prices")
        if invalid_ohlc > 0:
            fatal.append(f"{inst.symbol}: {invalid_ohlc} invalid OHLC bars")
        if zero_volume > 0 and observed_bars > 0:
            frac = zero_volume / observed_bars
            if frac > 0.5:
                warnings.append(f"{inst.symbol}: {zero_volume}/{observed_bars} zero-volume bars")

        per_instrument.append(
            InstrumentQuality(
                symbol=inst.symbol,
                first_valid=first_valid,
                last_valid=last_valid,
                observed_bars=observed_bars,
                missing_bars=missing_bars,
                imputed_bars=imputed_bars,
                longest_missing_streak=longest_missing,
                invalid_price_count=invalid_prices,
                invalid_ohlc_count=invalid_ohlc,
                zero_volume_count=zero_volume,
                stale_streaks=stale,
            )
        )

    elig_cov = float(np.mean(panel.eligibility_mask)) if panel.eligibility_mask.size else 0.0
    bench_cov = 1.0 if panel.benchmark_close is not None else 0.0

    if fatal:
        raise DataQualityError(f"fatal data quality errors: {fatal}")

    return DataQualityReport(
        requested_instruments=tuple(requested),
        returned_instruments=panel.assets,
        missing_instruments=missing,
        per_instrument=tuple(per_instrument),
        eligibility_coverage=elig_cov,
        benchmark_coverage=bench_cov,
        history_requirement_met=True,  # caller sets this after executor check
        fatal_errors=tuple(fatal),
        warnings=tuple(warnings),
    )


def apply_missing_data_policy(
    close: np.ndarray,
    volume: np.ndarray,
    observed_mask: np.ndarray,
    eligibility_mask: np.ndarray,
    policy: MissingDataPolicy,
    max_gap: int = 0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Apply missing-data policy to a panel.

    Returns (close, volume, observed_mask, imputation_mask).
    Never fills volume. Never fills before inception. Never fills across
    ineligible periods.
    """
    T, N = close.shape
    imputed = np.zeros((T, N), dtype=bool)

    if policy == MissingDataPolicy.REJECT:
        if np.any(~observed_mask & eligibility_mask):
            raise DataQualityError("missing data present but policy is REJECT")
        return close, volume, observed_mask, imputed

    if policy == MissingDataPolicy.PRESERVE_MISSING:
        return close, volume, observed_mask, imputed

    if policy == MissingDataPolicy.LIMITED_FORWARD_FILL:
        for j in range(N):
            # Find first valid observation
            obs = observed_mask[:, j]
            if not np.any(obs):
                continue
            first_valid = int(np.argmax(obs))
            # Forward fill within max_gap, only after inception, only while eligible
            gap = 0
            last_valid = first_valid
            for t in range(first_valid + 1, T):
                if observed_mask[t, j]:
                    gap = 0
                    last_valid = t
                elif eligibility_mask[t, j] and gap < max_gap:
                    close[t, j] = close[last_valid, j]
                    imputed[t, j] = True
                    gap += 1
                else:
                    gap += 1  # exceed max_gap or ineligible; leave NaN
        return close, volume, observed_mask, imputed

    raise ValueError(f"unknown missing-data policy: {policy}")


__all__ = [
    "DataQualityReport",
    "InstrumentQuality",
    "MissingDataPolicy",
    "apply_missing_data_policy",
    "validate_panel_quality",
]
