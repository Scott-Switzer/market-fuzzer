from __future__ import annotations

import json

import pandas as pd
import pytest

from app.calibration import (
    build_demo_calibration_pack,
    calibrate_bootstrap,
    compile_canonical_csv,
)


def test_demo_pack_is_deterministic_aggregate_only_and_chronological() -> None:
    first = build_demo_calibration_pack(seed=8, rows=120)
    second = build_demo_calibration_pack(seed=8, rows=120)

    assert first == second
    assert first.raw_rows_retained is False
    assert [window.row_count for window in first.windows] == [72, 24, 24]
    assert first.windows[0].end < first.windows[1].start < first.windows[2].start
    serialized = json.dumps(first.model_dump(mode="json"))
    assert '"raw_rows_retained": false' in serialized
    assert "records" not in serialized
    assert "signed_volume" in first.canonical_columns


def test_canonical_csv_compiles_and_retains_no_rows(tmp_path) -> None:
    rows = 50
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=rows, freq="min", tz="UTC"),
            "price": [100 + index * 0.01 for index in range(rows)],
            "spread_bps": [6 + (index % 3) for index in range(rows)],
            "bid_depth": [400 + index for index in range(rows)],
            "ask_depth": [420 + index for index in range(rows)],
            "volume": [100 + index for index in range(rows)],
            "signed_volume": [(-1) ** index * (40 + index / 2) for index in range(rows)],
        }
    )
    path = tmp_path / "canonical.csv"
    frame.to_csv(path, index=False)

    pack = compile_canonical_csv(path)

    assert pack.source_kind == "canonical_user_csv"
    assert [window.row_count for window in pack.windows] == [30, 10, 10]
    assert set(pack.windows[0].metrics) >= {
        "return_std",
        "spread_bps_mean",
        "total_depth_mean",
        "order_flow_autocorrelation_lag1",
    }
    assert "rows" not in pack.model_dump()


def test_csv_rejects_nonchronological_or_noncanonical_input(tmp_path) -> None:
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=30, freq="min", tz="UTC"),
            "price": [100.0] * 30,
            "spread_bps": [5.0] * 30,
            "bid_depth": [100] * 30,
            "ask_depth": [100] * 30,
            "volume": [50] * 30,
            "signed_volume": [10] * 30,
        }
    )
    frame.loc[4, "timestamp"] = frame.loc[2, "timestamp"]
    path = tmp_path / "bad.csv"
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="strictly chronological"):
        compile_canonical_csv(path)


def test_quick_bootstrap_has_three_accepted_sets_rejection_evidence_and_intervals() -> None:
    pack = build_demo_calibration_pack(seed=9, rows=300)
    result = calibrate_bootstrap(pack, mode="quick", seed=4)

    assert result.requested_bootstraps == 3
    assert len(result.accepted_parameter_sets) == 3
    assert result.rejected_parameter_sets
    assert set(result.bootstrap_intervals) == {
        "volatility_sensitivity",
        "base_order_size",
        "flow_persistence",
        "limit_intensity",
    }
    assert all(
        interval.identifiable in {"strong", "moderate", "weak"}
        for interval in result.bootstrap_intervals.values()
    )
    assert result.heldout_stability.median_heldout_distance >= 0


def test_bootstrap_bounds_and_determinism() -> None:
    pack = build_demo_calibration_pack()
    assert calibrate_bootstrap(pack, mode="audit", bootstraps=7, seed=3) == calibrate_bootstrap(
        pack, mode="audit", bootstraps=7, seed=3
    )
    with pytest.raises(ValueError, match="exactly 3"):
        calibrate_bootstrap(pack, mode="quick", bootstraps=4)
    with pytest.raises(ValueError, match="between 1 and 10"):
        calibrate_bootstrap(pack, mode="audit", bootstraps=11)
