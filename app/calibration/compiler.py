from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from .models import AggregateWindow, CalibrationObjective, CalibrationPackV1, MetricEstimate

CANONICAL_COLUMNS = (
    "timestamp",
    "price",
    "spread_bps",
    "bid_depth",
    "ask_depth",
    "volume",
    "signed_volume",
)


def _estimate(value: float, standard_error: float, unit: str = "unitless") -> MetricEstimate:
    width = 1.96 * max(0.0, standard_error)
    return MetricEstimate(
        value=float(value),
        standard_error=float(max(0.0, standard_error)),
        ci_lower=float(value - width),
        ci_upper=float(value + width),
        unit=unit,
    )


def _mean_estimate(values: np.ndarray, unit: str = "unitless") -> MetricEstimate:
    clean = values[np.isfinite(values)]
    if not len(clean):
        return _estimate(0.0, 0.0, unit)
    se = float(np.std(clean, ddof=1) / np.sqrt(len(clean))) if len(clean) > 1 else 0.0
    return _estimate(float(np.mean(clean)), se, unit)


def _lag_one(values: np.ndarray) -> float:
    clean = values[np.isfinite(values)]
    if len(clean) < 3 or np.std(clean[:-1]) == 0 or np.std(clean[1:]) == 0:
        return 0.0
    return float(np.corrcoef(clean[:-1], clean[1:])[0, 1])


def _aggregate_window(frame: pd.DataFrame, name: Literal["train", "validation", "test"]) -> AggregateWindow:
    price = frame["price"].to_numpy(dtype=float)
    returns = np.diff(np.log(price))
    abs_returns = np.abs(returns)
    total_depth = (frame["bid_depth"] + frame["ask_depth"]).to_numpy(dtype=float)
    volume = frame["volume"].to_numpy(dtype=float)
    signed_volume = frame["signed_volume"].to_numpy(dtype=float)
    imbalance = np.divide(signed_volume, volume, out=np.zeros_like(volume), where=volume > 0)
    return_std = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    return_se = return_std / np.sqrt(max(2.0 * (len(returns) - 1), 1.0))
    metrics = {
        "return_mean": _mean_estimate(returns, "log_return"),
        "return_std": _estimate(return_std, return_se, "log_return"),
        "absolute_return_autocorrelation_lag1": _estimate(
            _lag_one(abs_returns), 1 / np.sqrt(max(len(returns), 1))
        ),
        "spread_bps_mean": _mean_estimate(frame["spread_bps"].to_numpy(dtype=float), "bps"),
        "total_depth_mean": _mean_estimate(total_depth, "shares"),
        "volume_mean": _mean_estimate(volume, "shares"),
        "order_imbalance_mean": _mean_estimate(imbalance),
        "order_flow_autocorrelation_lag1": _estimate(
            _lag_one(imbalance), 1 / np.sqrt(max(len(imbalance), 1))
        ),
    }
    return AggregateWindow(
        name=name,
        start=frame["timestamp"].iloc[0].to_pydatetime(),
        end=frame["timestamp"].iloc[-1].to_pydatetime(),
        row_count=len(frame),
        metrics=metrics,
    )


def _compile_frame(
    frame: pd.DataFrame,
    *,
    pack_id: str,
    source_kind: Literal["deterministic_demo", "canonical_user_csv"],
    checksum: str,
    source_url: str,
    retrieval_date: date,
    usage_basis: str,
    instrument: str,
    venue: str,
    session: str,
) -> CalibrationPackV1:
    missing = sorted(set(CANONICAL_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"canonical calibration CSV missing columns: {missing}")
    if list(frame.columns) != list(CANONICAL_COLUMNS):
        raise ValueError(f"canonical calibration CSV columns must be exactly {list(CANONICAL_COLUMNS)}")
    frame = frame.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    numeric = CANONICAL_COLUMNS[1:]
    frame[list(numeric)] = frame[list(numeric)].apply(pd.to_numeric, errors="raise")
    if frame["timestamp"].duplicated().any() or not frame["timestamp"].is_monotonic_increasing:
        raise ValueError("timestamps must be unique and strictly chronological")
    if len(frame) < 30:
        raise ValueError("canonical calibration CSV requires at least 30 rows")
    if (frame["price"] <= 0).any() or (frame["spread_bps"] < 0).any():
        raise ValueError("price must be positive and spread_bps must be non-negative")
    if (frame[["bid_depth", "ask_depth", "volume"]] < 0).any().any():
        raise ValueError("depth and volume columns must be non-negative")
    if (frame["signed_volume"].abs() > frame["volume"]).any():
        raise ValueError("absolute signed_volume cannot exceed volume")

    first = int(len(frame) * 0.6)
    second = int(len(frame) * 0.8)
    windows = (
        _aggregate_window(frame.iloc[:first], "train"),
        _aggregate_window(frame.iloc[first:second], "validation"),
        _aggregate_window(frame.iloc[second:], "test"),
    )
    objectives = (
        CalibrationObjective(
            parameter="volatility_sensitivity", metric="return_std", distance="relative", tolerance=0.35
        ),
        CalibrationObjective(
            parameter="base_order_size", metric="total_depth_mean", distance="relative", tolerance=0.30
        ),
        CalibrationObjective(
            parameter="flow_persistence",
            metric="order_flow_autocorrelation_lag1",
            distance="absolute",
            tolerance=0.35,
        ),
        CalibrationObjective(
            parameter="limit_intensity", metric="spread_bps_mean", distance="relative", tolerance=0.25
        ),
    )
    return CalibrationPackV1(
        pack_id=pack_id,
        source_kind=source_kind,
        source_url=source_url,
        retrieval_date=retrieval_date,
        checksum=f"sha256:{checksum}",
        usage_basis=usage_basis,
        instrument=instrument,
        venue=venue,
        session=session,
        canonical_columns=CANONICAL_COLUMNS,
        windows=windows,
        objectives=objectives,
        notes=(
            "Rows were used transiently for chronological aggregation and are not retained.",
            "This pack describes aggregate evidence, not institutional calibration.",
        ),
    )


def compile_canonical_csv(
    path: str | Path,
    *,
    pack_id: str = "user-aggregate-v1",
    source_url: str = "user-provided://canonical-csv",
    retrieval_date: date = date(2026, 7, 14),
    usage_basis: str = "User supplied data authorized for aggregate calibration",
    instrument: str = "user-specified instrument",
    venue: str = "user-specified venue",
    session: str = "user-specified session",
) -> CalibrationPackV1:
    """Compile a canonical CSV into an aggregate-only pack with a 60/20/20 time split."""
    csv_path = Path(path)
    payload = csv_path.read_bytes()
    frame = pd.read_csv(csv_path)
    return _compile_frame(
        frame,
        pack_id=pack_id,
        source_kind="canonical_user_csv",
        checksum=hashlib.sha256(payload).hexdigest(),
        source_url=source_url,
        retrieval_date=retrieval_date,
        usage_basis=usage_basis,
        instrument=instrument,
        venue=venue,
        session=session,
    )


def build_demo_calibration_pack(seed: int = 20260714, rows: int = 300) -> CalibrationPackV1:
    """Build deterministic plausible aggregates from an ephemeral synthetic tape."""
    if rows < 30:
        raise ValueError("demo calibration pack requires at least 30 rows")
    rng = np.random.default_rng(seed)
    timestamps = pd.date_range("2026-01-05 14:30:00+00:00", periods=rows, freq="5s")
    volatility = np.empty(rows)
    innovations = np.empty(rows)
    volatility[0] = 0.0007
    innovations[0] = rng.normal()
    for index in range(1, rows):
        innovations[index] = 0.16 * innovations[index - 1] + rng.normal()
        volatility[index] = 0.00025 + 0.72 * volatility[index - 1] + 0.00010 * abs(innovations[index - 1])
    returns = volatility * innovations
    price = 100.0 * np.exp(np.cumsum(returns))
    signed_direction = np.where(innovations >= 0, 1.0, -1.0)
    volume = rng.integers(80, 650, rows).astype(float)
    signed_volume = signed_direction * volume * rng.uniform(0.2, 0.95, rows)
    spread = np.maximum(2.0, 7.0 + 2_500 * volatility + rng.normal(0, 0.55, rows))
    depth_base = np.maximum(80.0, 1_500 - 45 * spread + rng.normal(0, 80, rows))
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "price": price,
            "spread_bps": spread,
            "bid_depth": depth_base * rng.uniform(0.42, 0.58, rows),
            "ask_depth": depth_base * rng.uniform(0.42, 0.58, rows),
            "volume": volume,
            "signed_volume": signed_volume,
        }
    )
    fingerprint = hashlib.sha256(f"deterministic-demo-v1:{seed}:{rows}".encode()).hexdigest()
    return _compile_frame(
        frame,
        pack_id=f"deterministic-demo-{seed}-{rows}",
        source_kind="deterministic_demo",
        checksum=fingerprint,
        source_url="synthetic://deterministic-demo-tape-v1",
        retrieval_date=date(2026, 7, 14),
        usage_basis="Internally generated synthetic data approved for demonstration",
        instrument="SYN-DEMO",
        venue="SMW synthetic exchange",
        session="continuous synthetic session",
    )
