"""Build an aggregate-only calibration pack from a local intraday parquet source."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.calibration import compile_local_ohlcv_parquet


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--security-id", type=int, default=None)
    parser.add_argument("--timeframe", default="1Min")
    parser.add_argument("--pack-id", default="local-ohlcv-proxy-v1")
    parser.add_argument("--instrument", default="local-instrument")
    parser.add_argument("--venue", default="local-market-data")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pack = compile_local_ohlcv_parquet(
        args.path,
        security_id=args.security_id,
        timeframe=args.timeframe,
        pack_id=args.pack_id,
        instrument=args.instrument,
        venue=args.venue,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(pack.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pack_id": pack.pack_id, "checksum": pack.checksum, "output": str(args.output)}))


if __name__ == "__main__":
    main()
