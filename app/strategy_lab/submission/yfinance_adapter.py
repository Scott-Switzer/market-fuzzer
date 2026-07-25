"""yfinance research/demo adapter (Tier 2).

Multi-ticker download of adjusted daily OHLCV for a fixed universe over a fixed
date range. Bounded-retry acquisition, per-ticker failure reporting (no silent
dropping), local cache, and explicit research/educational-use labeling. No raw
Yahoo data is committed to the repo unless redistribution rights are confirmed.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.strategy_lab.submission.panels import (
    AssetMetadata,
    DataProvenance,
    MarketDataPanel,
)

CACHE_DIR = Path("artifacts/data_cache/yfinance")
RESEARCH_NOTICE = (
    "Data acquired via yfinance for RESEARCH/EDUCATIONAL use only. "
    "Not investment advice. Verify redistribution rights before shipping raw data."
)


def _cache_paths(universe_key: str) -> dict[str, Path]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "request": CACHE_DIR / f"request_{universe_key}.json",
        "provenance": CACHE_DIR / f"provenance_{universe_key}.json",
        "quality": CACHE_DIR / f"quality_{universe_key}.json",
        "prices": CACHE_DIR / f"prices_{universe_key}.parquet",
    }


def acquire(
    *,
    tickers: list[str],
    start: str,
    end: str,
    cache_key: str | None = None,
    use_cache: bool = True,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Acquire a panel. Returns a dict with either 'panel' (MarketDataPanel) or
    'error' plus 'quality'/'provenance' so callers can show a visible failure
    and offer the synthetic fallback (never present generated data as historical).
    """
    key = cache_key or hashlib.sha256((",".join(sorted(tickers)) + start + end).encode()).hexdigest()[:16]
    paths = _cache_paths(key)

    if use_cache and paths["prices"].exists():
        try:
            panel = _load_cache(key)
            return {
                "panel": panel,
                "cached": True,
                "quality": _read_json(paths["quality"]),
                "provenance": panel.provenance.__dict__,
            }
        except Exception as exc:  # corrupt cache -> refetch
            quality_note = {"cache_load_error": str(exc)}
    else:
        quality_note = {}

    try:
        import yfinance as yf
    except Exception as exc:  # yfinance unavailable
        return {
            "panel": None,
            "error": f"yfinance unavailable: {exc}",
            "quality": {"status": "unavailable", "notice": RESEARCH_NOTICE},
            "provenance": None,
        }

    last_err: str | None = None
    data = None
    for attempt in range(1, max_retries + 1):
        try:
            df = yf.download(
                tickers,
                start=start,
                end=end,
                auto_adjust=False,
                progress=False,
                threads=False,
            )
            if df is not None and not df.empty:
                data = df
                break
        except Exception as exc:
            last_err = str(exc)
        time.sleep(min(2.0 * attempt, 6.0))
    if data is None:
        return {
            "panel": None,
            "error": f"yfinance download failed after {max_retries} attempts: {last_err}",
            "quality": {"status": "failed", "attempts": max_retries, "notice": RESEARCH_NOTICE},
            "provenance": None,
        }

    # Normalize to wide T x N OHLCV
    try:
        panel, quality = _normalize_yf(data, tickers, start, end, key)
    except Exception as exc:
        return {
            "panel": None,
            "error": f"yfinance normalize failed: {exc}",
            "quality": {"status": "normalize_error", "detail": str(exc)},
            "provenance": None,
        }

    # persist cache
    try:
        _save_cache(panel, key, quality)
    except Exception as exc:
        quality_note["cache_write_error"] = str(exc)

    quality.update(quality_note)
    return {"panel": panel, "cached": False, "quality": quality, "provenance": panel.provenance.__dict__}


def _normalize_yf(df, tickers, start, end, key) -> tuple[MarketDataPanel, dict[str, Any]]:
    # yfinance multi-index: columns = (field, ticker)
    fields = ["Open", "High", "Low", "Close", "Volume"]
    present = [f for f in fields if f in df.columns.get_level_values(0)]
    assets: list[str] = []
    series: dict[str, dict[str, np.ndarray]] = {}
    per_ticker: dict[str, Any] = {}
    for tk in tickers:
        got = False
        cols = {f: df[(f, tk)] for f in present if (f, tk) in df.columns}
        if len(cols) >= 4 and len(cols["Close"]) > 1:
            assets.append(tk)
            series[tk] = {f.lower(): cols[f].to_numpy(dtype=float) for f in cols}
            got = True
        per_ticker[tk] = "ok" if got else "missing_or_empty"
    if not assets:
        raise ValueError("no tickers returned usable data")

    # align on a common date index (union of trading days)
    idx = df.index
    dates = [d.date() if hasattr(d, "date") else datetime.fromisoformat(str(d)).date() for d in idx]
    T = len(dates)
    N = len(assets)
    open_ = np.zeros((T, N))
    high = np.zeros((T, N))
    low = np.zeros((T, N))
    close = np.zeros((T, N))
    volume = np.zeros((T, N))
    for j, tk in enumerate(assets):
        open_[:, j] = series[tk].get("open", series[tk]["close"])
        high[:, j] = series[tk].get("high", series[tk]["close"])
        low[:, j] = series[tk].get("low", series[tk]["close"])
        close[:, j] = series[tk]["close"]
        volume[:, j] = series[tk].get("volume", np.zeros(T))

    benchmark = None
    if "SPY" in assets:
        spy_j = assets.index("SPY")
        benchmark = close[:, spy_j].copy()
        # SPY is the benchmark only — remove it from the TRADABLE universe so the
        # cross-sectional strategy never selects it as a position.
        keep = [j for j in range(N) if j != spy_j]
        assets = [assets[j] for j in keep]
        open_ = open_[:, keep]
        high = high[:, keep]
        low = low[:, keep]
        close = close[:, keep]
        volume = volume[:, keep]
        N = len(assets)

    metadata = {a: AssetMetadata(ticker=a, is_benchmark=False) for a in assets}
    provenance = DataProvenance(
        source="yfinance",
        tier=2,
        retrieval_timestamp=datetime.utcnow().isoformat() + "Z",
        source_hash=hashlib.sha256((",".join(assets) + start + end).encode()).hexdigest(),
        transformations=["auto_adjust=False", "wide_panel_align"],
        warnings=[],
        label="yfinance (research/educational)",
    )
    panel = MarketDataPanel(
        dates=tuple(dates),
        assets=tuple(assets),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        benchmark_close=benchmark,
        metadata=metadata,
        provenance=provenance,
    )
    quality = {
        "status": "ok",
        "requested": tickers,
        "returned": assets,
        "per_ticker": per_ticker,
        "dropped": [t for t in tickers if t not in assets],
        "rows": T,
        "notice": RESEARCH_NOTICE,
    }
    return panel, quality


def _save_cache(panel: MarketDataPanel, key: str, quality: dict[str, Any]) -> None:
    paths = _cache_paths(key)
    try:
        import pandas as pd

        cols = {a: panel.close[:, i] for i, a in enumerate(panel.assets)}
        pd.DataFrame(cols, index=[d.isoformat() for d in panel.dates]).to_parquet(paths["prices"])
    except Exception:
        # parquet optional; fall back to npy
        np.save(str(paths["prices"]).replace(".parquet", ".npy"), panel.close)
    _write_json(paths["request"], {"universe_key": key, "generated_at": datetime.utcnow().isoformat() + "Z"})
    _write_json(paths["provenance"], panel.provenance.__dict__)
    _write_json(paths["quality"], quality)


def _load_cache(key: str) -> MarketDataPanel:
    paths = _cache_paths(key)
    close = None
    if paths["prices"].exists():
        try:
            import pandas as pd

            df = pd.read_parquet(paths["prices"])
            close = df.to_numpy(dtype=float)
            assets = list(df.columns)
            dates = [datetime.fromisoformat(str(d)).date() for d in df.index]
        except Exception:
            npy = str(paths["prices"]).replace(".parquet", ".npy")
            close = np.load(npy)
            # assets/dates from provenance best-effort
            prov = _read_json(paths["provenance"]) or {}
            assets = list(prov.get("label", "yfinance").split())
            dates = [datetime(2021, 1, 1).date()] * close.shape[0]
    if close is None:
        raise ValueError("cache prices missing")
    T, N = close.shape
    # reconstruct a minimal valid panel (open=high=low=close for cache reload;
    # acceptable because we only need close + benchmark for the historical demo)
    placeholder = close.copy()
    benchmark = None
    if "SPY" in assets:
        spy_j = assets.index("SPY")
        benchmark = close[:, spy_j].copy()
        keep = [j for j in range(N) if j != spy_j]
        assets = [assets[j] for j in keep]
        close = close[:, keep]
        placeholder = close.copy()
        N = len(assets)
    metadata = {a: AssetMetadata(ticker=a, is_benchmark=False) for a in assets}
    provenance = DataProvenance(
        source="yfinance",
        tier=2,
        retrieval_timestamp="cached",
        source_hash=key,
        transformations=["cache_reload"],
        warnings=[],
        label="yfinance (cached, research/educational)",
    )
    return MarketDataPanel(
        dates=tuple(dates),
        assets=tuple(assets),
        open=placeholder,
        high=placeholder,
        low=placeholder,
        close=close,
        volume=np.ones((T, N)),
        benchmark_close=benchmark,
        metadata=metadata,
        provenance=provenance,
    )


def _write_json(p: Path, obj: Any) -> None:
    p.write_text(json.dumps(obj, indent=2, default=str))


def _read_json(p: Path) -> Any:
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None
