from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as parquet

from app.calibration import compile_local_ohlcv_parquet


def test_local_ohlcv_adapter_builds_explicit_proxy_pack(tmp_path: Path) -> None:
    source = tmp_path / "bars.parquet"
    frame = pd.DataFrame(
        {
            "security_id": [7] * 36,
            "timeframe": ["1Min"] * 36,
            "bar_time": pd.date_range("2026-01-01", periods=36, freq="min", tz="UTC"),
            "open_price": [100.0 + index * 0.01 for index in range(36)],
            "high_price": [100.1 + index * 0.01 for index in range(36)],
            "low_price": [99.9 + index * 0.01 for index in range(36)],
            "close_price": [100.05 + index * 0.01 for index in range(36)],
            "volume": [100.0 + index for index in range(36)],
        }
    )
    parquet.write_table(pa.Table.from_pandas(frame), source)
    pack = compile_local_ohlcv_parquet(source, security_id=7, pack_id="local-test-v1")
    assert pack.source_kind == "local_ohlcv_proxy"
    assert pack.window("train").row_count == 21
    assert pack.data_manifest is not None
    assert pack.data_manifest.resolution == "ohlcv"
    assert set(pack.data_manifest.prohibited_claims) >= {
        "queue_position",
        "fill_probability",
        "cancellation_behavior",
    }
    assert any("OHLCV-derived proxies" in note for note in pack.notes)
