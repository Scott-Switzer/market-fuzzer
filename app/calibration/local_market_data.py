"""Adapters for using local OHLCV research data as bounded calibration evidence."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as parquet

from .compiler import CANONICAL_COLUMNS, _compile_frame
from .models import (
    CalibrationDataManifestV1,
    CalibrationPackV1,
    DataResolutionV1,
    supported_properties_for_resolution,
)


def compile_local_ohlcv_parquet(
    path: str | Path,
    *,
    security_id: int | None = None,
    timeframe: str = "1Min",
    pack_id: str = "local-ohlcv-proxy-v1",
    instrument: str = "local-instrument",
    venue: str = "local-market-data",
    session: str = "observed-session",
    usage_basis: str = "Local research data authorized for aggregate regime calibration",
    max_rows: int = 100_000,
) -> CalibrationPackV1:
    """Compile local intraday bars into aggregate evidence without retaining source rows.

    OHLCV does not contain queue position or displayed depth. The derived spread,
    depth, and signed-flow columns are explicit proxies and remain labeled as
    such in the resulting calibration pack.
    """

    parquet_path = Path(path).expanduser().resolve()
    if not parquet_path.is_file():
        raise FileNotFoundError(parquet_path)
    if max_rows < 30:
        raise ValueError("max_rows must be at least 30")
    table = parquet.ParquetFile(parquet_path).read(
        columns=[
            "security_id",
            "timeframe",
            "bar_time",
            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "volume",
        ]
    )
    frame = table.to_pandas()
    frame = frame[frame["timeframe"].astype(str) == timeframe]
    if security_id is None:
        counts = frame.groupby("security_id", sort=True).size()
        eligible = counts[counts >= 30]
        if eligible.empty:
            raise ValueError("local parquet contains no security with at least 30 bars")
        security_id = int(eligible.sort_values(ascending=False).index[0])
    frame = frame[frame["security_id"] == security_id].sort_values("bar_time").tail(max_rows)
    if len(frame) < 30:
        raise ValueError(f"security_id {security_id} has fewer than 30 {timeframe} bars")

    close = frame["close_price"].astype(float).to_numpy()
    open_price = frame["open_price"].astype(float).to_numpy()
    high = frame["high_price"].astype(float).to_numpy()
    low = frame["low_price"].astype(float).to_numpy()
    volume = frame["volume"].astype(float).clip(lower=0).to_numpy()
    price_range_bps = np.divide(high - low, close, out=np.zeros_like(close), where=close > 0) * 10_000
    direction = np.sign(close - open_price)
    range_size = np.maximum(high - low, np.finfo(float).eps)
    conviction = np.clip(np.abs(close - open_price) / range_size, 0.2, 1.0)
    signed_volume = direction * volume * conviction
    depth_base = np.maximum(volume * 2.0, 1.0)
    imbalance = np.divide(signed_volume, volume, out=np.zeros_like(volume), where=volume > 0)
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(frame["bar_time"], utc=True),
            "price": close,
            "spread_bps": np.maximum(price_range_bps, 1.0),
            "bid_depth": depth_base * (1.0 + 0.25 * imbalance),
            "ask_depth": depth_base * (1.0 - 0.25 * imbalance),
            "volume": volume,
            "signed_volume": signed_volume,
        }
    )[list(CANONICAL_COLUMNS)]
    source_checksum = hashlib.sha256(parquet_path.read_bytes()).hexdigest()
    pack = _compile_frame(
        frame,
        pack_id=pack_id,
        source_kind="local_ohlcv_proxy",
        checksum=source_checksum,
        source_url=f"local-file://sha256/{source_checksum}",
        retrieval_date=date.today(),
        usage_basis=usage_basis,
        instrument=instrument,
        venue=venue,
        session=session,
    )
    return pack.model_copy(
        update={
            "data_manifest": CalibrationDataManifestV1(
                source_id=f"local-parquet:{parquet_path.name}",
                resolution=DataResolutionV1.OHLCV,
                source_checksum=f"sha256:{source_checksum}",
                rights_basis=usage_basis,
                source_row_count=len(frame),
                calibration_start=pack.window("train").start,
                calibration_end=pack.window("validation").end,
                heldout_start=pack.window("test").start,
                heldout_end=pack.window("test").end,
                supported_properties=supported_properties_for_resolution(DataResolutionV1.OHLCV),
                prohibited_claims=("queue_position", "fill_probability", "cancellation_behavior"),
            ),
            "notes": pack.notes
            + (
                f"Derived from security_id={security_id}, timeframe={timeframe}, and {len(frame)} OHLCV bars.",
                "Spread, depth, and signed-flow fields are OHLCV-derived proxies; no order-book rows were retained.",
            ),
        }
    )
