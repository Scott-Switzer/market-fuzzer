"""Run-of-record: acquire 30-name universe + SPY via yfinance adapter, report quality,
and write a cache manifest + hash under artifacts/yfinance_cache/.

Read-only on app/. Usage:
    env -u PYTHONPATH .venv312/bin/python tests/submission_audit/run_of_record_yf.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

from app.strategy_lab.submission.strategy import (  # noqa: E402
    BENCHMARK,
    DEMO_UNIVERSE,
    FIXED_END,
    FIXED_START,
)
from app.strategy_lab.submission.yfinance_adapter import CACHE_DIR, acquire  # noqa: E402

OUT_DIR = REPO / "artifacts" / "yfinance_cache"


def main() -> int:
    tickers = list(DEMO_UNIVERSE) + [BENCHMARK]  # 30 names + SPY
    res = acquire(tickers=tickers, start=FIXED_START, end=FIXED_END, use_cache=True)
    quality = res.get("quality") or {}
    panel = res.get("panel")

    manifest: dict = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "universe": list(DEMO_UNIVERSE),
        "benchmark": BENCHMARK,
        "requested_tickers": tickers,
        "requested_count": len(tickers),
        "start": FIXED_START,
        "end": FIXED_END,
        "cached": res.get("cached"),
        "error": res.get("error"),
        "quality": quality,
        "adapter_cache_dir": str(CACHE_DIR),
    }

    if panel is not None:
        returned = list(panel.assets)
        close = panel.close
        missing = float(np.isnan(close).mean()) if close.size else None
        manifest.update(
            {
                "returned_count": len(returned),
                "returned": returned,
                "failed": [t for t in tickers if t not in returned],
                "first_date": panel.dates[0].isoformat(),
                "last_date": panel.dates[-1].isoformat(),
                "rows": panel.T,
                "missing_fraction_close": missing,
                "survivorship_warning": (
                    "Universe fixed as of selection date; delisted names absent -> "
                    "survivorship bias for pre-2026 history."
                ),
                "provenance": res.get("provenance"),
            }
        )
        payload = json.dumps(
            {"assets": returned, "rows": panel.T, "first": manifest["first_date"], "last": manifest["last_date"]},
            sort_keys=True,
        ).encode()
        manifest["panel_hash"] = hashlib.sha256(close.tobytes() + payload).hexdigest()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "run_of_record_manifest.json"
    out.write_text(json.dumps(manifest, indent=2, default=str))
    print(json.dumps({k: v for k, v in manifest.items() if k not in ("quality", "provenance")}, indent=2, default=str))
    print("manifest ->", out)
    return 0 if panel is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
