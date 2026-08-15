"""Canonical MarketDataPanel (Phase 3).

Single authoritative panel contract for all strategy execution. Contains full
provenance, eligibility masks, observed/imputed masks, and dataset digest.
Validates all invariants at construction time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np

from app.market_data.adjustments import AdjustmentPolicy
from app.market_data.calendar import CalendarPolicy
from app.market_data.contracts import EligibilitySource, InstrumentIdentifier
from app.market_data.errors import DataQualityError


@dataclass(frozen=True)
class MarketDataPanel:
    """Canonical T×N market-data panel with full provenance and masks.

    All arrays are aligned: ``dates`` (T,), ``instruments`` (N,),
    ``open/high/low/close/volume`` (T×N), ``benchmark_close`` (T,) or None,
    ``eligibility_mask`` (T×N), ``observed_mask`` (T×N), ``imputation_mask`` (T×N).
    """

    # Core data
    dates: tuple[date, ...]
    instruments: tuple[InstrumentIdentifier, ...]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    # Benchmark (separate from tradable assets)
    benchmark_close: np.ndarray | None
    benchmark_instrument: InstrumentIdentifier | None

    # Masks (T×N boolean)
    eligibility_mask: np.ndarray
    observed_mask: np.ndarray
    imputation_mask: np.ndarray

    # Provenance
    provider: str
    provider_version: str
    retrieval_timestamp: datetime
    as_of: datetime | None
    calendar_policy: CalendarPolicy
    adjustment_policy: AdjustmentPolicy
    missing_data_policy: str
    eligibility_source: EligibilitySource
    source_metadata: dict[str, Any] = field(default_factory=dict)

    # Digest (computed at construction)
    dataset_digest: str = field(init=False)

    def __post_init__(self) -> None:
        self._validate()
        # Compute digest after validation
        from app.market_data.digest import compute_dataset_digest

        object.__setattr__(self, "dataset_digest", compute_dataset_digest(self))

    # --- validation ---------------------------------------------------------
    def _validate(self) -> None:
        T = len(self.dates)
        N = len(self.instruments)

        # Unique, increasing timestamps
        if len({d for d in self.dates}) != T:
            raise DataQualityError("duplicate timestamps in panel")
        for i in range(1, T):
            if self.dates[i] <= self.dates[i - 1]:
                raise DataQualityError(f"timestamps not strictly increasing at index {i}")

        # Unique instruments
        stable_ids = [i.stable_id for i in self.instruments]
        if len(set(stable_ids)) != N:
            raise DataQualityError("duplicate stable instrument IDs")

        # Shape alignment
        for name in (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "eligibility_mask",
            "observed_mask",
            "imputation_mask",
        ):
            arr = getattr(self, name)
            if arr.shape != (T, N):
                raise DataQualityError(f"{name} shape {arr.shape} != ({T}, {N})")

        # Finite prices where observed
        for name in ("open", "high", "low", "close"):
            arr = getattr(self, name)
            obs = self.observed_mask
            if np.any(~np.isfinite(arr[obs])):
                raise DataQualityError(f"non-finite {name} at observed positions")
            if np.any(arr[obs] <= 0):
                raise DataQualityError(f"non-positive {name} at observed positions")

        # Volume non-negative where observed
        if np.any(self.volume[self.observed_mask] < 0):
            raise DataQualityError("negative volume at observed positions")

        # OHLC invariants where observed
        obs = self.observed_mask
        if np.any(self.high[obs] < np.maximum(self.open[obs], self.close[obs])):
            raise DataQualityError("high < max(open, close) at observed positions")
        if np.any(self.low[obs] > np.minimum(self.open[obs], self.close[obs])):
            raise DataQualityError("low > min(open, close) at observed positions")
        if np.any(self.high[obs] < self.low[obs]):
            raise DataQualityError("high < low at observed positions")

        # No imputation marked as observation
        if np.any(self.imputation_mask & self.observed_mask):
            raise DataQualityError("imputed bars marked as observed")

        # Benchmark alignment
        if self.benchmark_close is not None:
            if self.benchmark_close.shape != (T,):
                raise DataQualityError(f"benchmark_close shape {self.benchmark_close.shape} != ({T},)")
            if self.benchmark_instrument is None:
                raise DataQualityError("benchmark_close present but benchmark_instrument is None")

        # No eligibility before first valid observation (inception)
        for j in range(N):
            eligible = self.eligibility_mask[:, j]
            observed = self.observed_mask[:, j]
            if not np.any(observed):
                continue  # no data for this instrument
            first_obs = np.argmax(observed)
            if np.any(eligible[:first_obs]):
                raise DataQualityError(
                    f"instrument {self.instruments[j].symbol} eligible before first observation"
                )

    # --- convenience ---------------------------------------------------------
    @property
    def T(self) -> int:
        return len(self.dates)

    @property
    def N(self) -> int:
        return len(self.instruments)

    @property
    def assets(self) -> tuple[str, ...]:
        """Backward-compatible symbol list."""
        return tuple(i.symbol for i in self.instruments)

    def asset_index(self, symbol: str) -> int:
        for j, inst in enumerate(self.instruments):
            if inst.symbol == symbol:
                return j
        raise KeyError(symbol)

    def instrument_index(self, stable_id: str) -> int:
        for j, inst in enumerate(self.instruments):
            if inst.stable_id == stable_id:
                return j
        raise KeyError(stable_id)

    def simple_returns(self) -> np.ndarray:
        """Close-to-close returns for each asset (T-1 x N)."""
        return self.close[1:] / self.close[:-1] - 1.0

    def eligible_assets(self, t: int) -> list[str]:
        """Symbols eligible at time t."""
        return [self.instruments[j].symbol for j in range(self.N) if self.eligibility_mask[t, j]]

    def observed_assets(self, t: int) -> list[str]:
        """Symbols with observed data at time t."""
        return [self.instruments[j].symbol for j in range(self.N) if self.observed_mask[t, j]]

    # --- serialization -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "dates": [d.isoformat() for d in self.dates],
            "instruments": [
                {
                    "symbol": i.symbol,
                    "venue": i.venue,
                    "asset_type": i.asset_type.value,
                    "currency": i.currency,
                    "provider_symbol": i.provider_symbol,
                    "stable_id": i.stable_id,
                }
                for i in self.instruments
            ],
            "open": self.open.tolist(),
            "high": self.high.tolist(),
            "low": self.low.tolist(),
            "close": self.close.tolist(),
            "volume": self.volume.tolist(),
            "benchmark_close": self.benchmark_close.tolist() if self.benchmark_close is not None else None,
            "benchmark_instrument": (
                {
                    "symbol": self.benchmark_instrument.symbol,
                    "venue": self.benchmark_instrument.venue,
                    "asset_type": self.benchmark_instrument.asset_type.value,
                    "currency": self.benchmark_instrument.currency,
                    "provider_symbol": self.benchmark_instrument.provider_symbol,
                    "stable_id": self.benchmark_instrument.stable_id,
                }
                if self.benchmark_instrument
                else None
            ),
            "eligibility_mask": self.eligibility_mask.tolist(),
            "observed_mask": self.observed_mask.tolist(),
            "imputation_mask": self.imputation_mask.tolist(),
            "provider": self.provider,
            "provider_version": self.provider_version,
            "retrieval_timestamp": self.retrieval_timestamp.isoformat(),
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "calendar_policy": self.calendar_policy.value,
            "adjustment_policy": self.adjustment_policy.value,
            "missing_data_policy": self.missing_data_policy,
            "eligibility_source": self.eligibility_source.value,
            "source_metadata": self.source_metadata,
            "dataset_digest": self.dataset_digest,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> MarketDataPanel:
        from app.market_data.contracts import AssetType

        instruments = tuple(
            InstrumentIdentifier(
                symbol=i["symbol"],
                venue=i.get("venue"),
                asset_type=AssetType(i.get("asset_type", "equity")),
                currency=i.get("currency", "USD"),
                provider_symbol=i.get("provider_symbol"),
                stable_id=i.get("stable_id"),
            )
            for i in payload["instruments"]
        )
        bench_inst = payload.get("benchmark_instrument")
        benchmark_instrument = (
            InstrumentIdentifier(
                symbol=bench_inst["symbol"],
                venue=bench_inst.get("venue"),
                asset_type=AssetType(bench_inst.get("asset_type", "equity")),
                currency=bench_inst.get("currency", "USD"),
                provider_symbol=bench_inst.get("provider_symbol"),
                stable_id=bench_inst.get("stable_id"),
            )
            if bench_inst
            else None
        )
        return cls(
            dates=tuple(date.fromisoformat(d) for d in payload["dates"]),
            instruments=instruments,
            open=np.asarray(payload["open"], dtype=float),
            high=np.asarray(payload["high"], dtype=float),
            low=np.asarray(payload["low"], dtype=float),
            close=np.asarray(payload["close"], dtype=float),
            volume=np.asarray(payload["volume"], dtype=float),
            benchmark_close=np.asarray(payload["benchmark_close"], dtype=float)
            if payload.get("benchmark_close") is not None
            else None,
            benchmark_instrument=benchmark_instrument,
            eligibility_mask=np.asarray(payload["eligibility_mask"], dtype=bool),
            observed_mask=np.asarray(payload["observed_mask"], dtype=bool),
            imputation_mask=np.asarray(payload["imputation_mask"], dtype=bool),
            provider=payload["provider"],
            provider_version=payload["provider_version"],
            retrieval_timestamp=datetime.fromisoformat(payload["retrieval_timestamp"]),
            as_of=datetime.fromisoformat(payload["as_of"]) if payload.get("as_of") else None,
            calendar_policy=CalendarPolicy(payload["calendar_policy"]),
            adjustment_policy=AdjustmentPolicy(payload["adjustment_policy"]),
            missing_data_policy=payload["missing_data_policy"],
            eligibility_source=EligibilitySource(payload["eligibility_source"]),
            source_metadata=payload.get("source_metadata", {}),
        )


__all__ = ["MarketDataPanel"]
