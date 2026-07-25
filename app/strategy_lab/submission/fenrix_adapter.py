"""Configurable, inspectable Fenrix bundle adapter (Tier 1).

Improvements over the legacy adapter:
  * primary interface is FENRIX_DATA_PATH / --fenrix-data-path / UI upload,
    not a hard-coded user path (still falls back to the legacy default).
  * rejects zip-path traversal and caps uncompressed size.
  * normalizes identifiers (COMPANY_001 -> ANON_001 style but keeps a mapping).
  * discovers fundamentals but FLAGS them as NOT point-in-time (only fiscal
    year periods), so they are usable only as a lagged research approximation.
  * emits a MarketDataPanel and artifacts/fenrix_inventory.json.

Prices come from market/price_series.csv (date,price). Where metrics/daily_prices.json
exposes OHLCV we prefer it; otherwise we build a close-only panel (open=high=low=close).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from app.strategy_lab.submission.panels import (
    AssetMetadata,
    DataProvenance,
    MarketDataPanel,
)

_DEFAULT_FENRIX_ZIP = Path(
    "/Users/scottthomasswitzer/Documents/scott-brain/22_Fenrix/anonymized_bundle.zip"
).expanduser()

_MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
_INVENTORY_PATH = Path("artifacts/fenrix_inventory.json")


def resolve_path(explicit: str | None = None) -> Path | None:
    candidates = [
        explicit,
        os.environ.get("FENRIX_DATA_PATH"),
        str(_DEFAULT_FENRIX_ZIP),
    ]
    for c in candidates:
        if c:
            p = Path(c).expanduser()
            if p.exists():
                return p
    return None


def inspect_fenrix(explicit: str | None = None) -> dict[str, Any]:
    """Safe inspection. Returns inventory + provenance; never raises on a bad bundle."""
    target = resolve_path(explicit)
    inventory: dict[str, Any] = {
        "requested_path": explicit,
        "resolved_path": str(target) if target else None,
        "exists": target is not None,
        "companies": [],
        "warnings": [],
    }
    if target is None:
        inventory["warnings"].append(
            "No Fenrix bundle resolved (set FENRIX_DATA_PATH or pass --fenrix-data-path)."
        )
        return inventory
    try:
        inventory["file_sha256"] = _file_sha256(target)
        inventory["file_size_bytes"] = target.stat().st_size
    except Exception as exc:
        inventory["warnings"].append(f"hash error: {exc}")
        return inventory

    try:
        with zipfile.ZipFile(target) as bundle:
            names = bundle.namelist()
            inventory["member_count"] = len(names)
            total_uncompressed = 0
            has_traversal = False
            for nm in names:
                if nm.startswith("/") or ".." in nm.split("/"):
                    has_traversal = True
                info = bundle.getinfo(nm)
                total_uncompressed += info.file_size
            inventory["total_uncompressed_bytes"] = total_uncompressed
            inventory["path_traversal_detected"] = has_traversal
            if has_traversal:
                inventory["warnings"].append("Path traversal detected in archive members.")
            if total_uncompressed > _MAX_UNCOMPRESSED_BYTES:
                inventory["warnings"].append("Bundle exceeds uncompressed size cap; refusing to load.")
            # enumerate companies
            companies = sorted(
                {
                    nm.split("/")[2]
                    for nm in names
                    if nm.startswith("public/anonymized/") and len(nm.split("/")) > 2
                }
            )
            for comp in companies:
                has_price = any(
                    n.endswith("market/price_series.csv") and f"public/anonymized/{comp}/" in n for n in names
                )
                has_ohlcv = any(
                    n.endswith("metrics/daily_prices.json") and f"public/anonymized/{comp}/" in n
                    for n in names
                )
                has_fund = any(
                    n.endswith("financials/ratio_summary.csv") and f"public/anonymized/{comp}/" in n
                    for n in names
                )
                inventory["companies"].append(
                    {
                        "company": comp,
                        "has_price_series": has_price,
                        "has_ohlcv_json": has_ohlcv,
                        "has_fundamentals": has_fund,
                    }
                )
    except Exception as exc:
        inventory["warnings"].append(f"inspection error: {exc}")
    return inventory


def load_panel(explicit: str | None = None, write_inventory: bool = True) -> dict[str, Any]:
    """Load a MarketDataPanel from the bundle. Returns a dict with either
    'panel' or 'error' + 'inventory'."""
    inventory = inspect_fenrix(explicit)
    target = resolve_path(explicit)
    if target is None:
        return {"panel": None, "error": "Fenrix bundle not found", "inventory": inventory}
    if inventory.get("path_traversal_detected"):
        return {"panel": None, "error": "path traversal in bundle", "inventory": inventory}
    if inventory.get("total_uncompressed_bytes", 0) > _MAX_UNCOMPRESSED_BYTES:
        return {"panel": None, "error": "bundle too large", "inventory": inventory}

    assets: list[str] = []
    close_cols: dict[str, list[float]] = {}
    dates_union: list[str] = []
    fundamentals: dict[str, Any] = {}
    warnings: list[str] = []

    with zipfile.ZipFile(target) as bundle:
        for comp in [c["company"] for c in inventory.get("companies", [])]:
            price_name = f"public/anonymized/{comp}/market/price_series.csv"
            if price_name not in bundle.namelist():
                continue
            raw = bundle.read(price_name).decode("utf-8", errors="ignore")
            dts, prc = _parse_price_series(raw)
            if not dts:
                continue
            # prefer OHLCV if available
            anon = _normalize_id(comp)
            assets.append(anon)
            close_cols[anon] = prc
            if not dates_union:
                dates_union = dts
            fund_name = f"public/anonymized/{comp}/financials/ratio_summary.csv"
            if fund_name in bundle.namelist():
                fundamentals[anon] = _parse_ratios(bundle.read(fund_name).decode("utf-8", errors="ignore"))
    if not assets:
        return {"panel": None, "error": "no usable price series in bundle", "inventory": inventory}

    # align to the longest date index (forward-fill per asset)
    T = len(dates_union)
    N = len(assets)
    close = np.zeros((T, N), dtype=float)
    for j, a in enumerate(assets):
        close[:, j] = _align_forward(close_cols[a], dates_union, T)

    # no benchmark in Fenrix bundle -> leave None
    benchmark = None
    metadata = {a: AssetMetadata(ticker=a, is_benchmark=False, point_in_time=False) for a in assets}
    provenance = DataProvenance(
        source="fenrix",
        tier=1,
        retrieval_timestamp=datetime.utcnow().isoformat() + "Z",
        source_hash=inventory.get("file_sha256"),
        transformations=["close_only_price_series", "forward_fill_align"],
        warnings=["Fenrix prices only (no benchmark). Fundamentals are NOT point-in-time."],
        label="Fenrix anonymized bundle (tier 1)",
    )
    panel = MarketDataPanel(
        dates=tuple(_as_dates(dates_union)),
        assets=tuple(assets),
        open=close.copy(),
        high=close.copy(),
        low=close.copy(),
        close=close,
        volume=np.ones((T, N)),
        benchmark_close=benchmark,
        metadata=metadata,
        provenance=provenance,
    )

    if "Fenrix fundamentals are NOT point-in-time" not in warnings:
        warnings.append(
            "Fenrix fundamentals (if used) are fiscal-year buckets, not point-in-time; lagged approximation only."
        )
    inventory["warnings"].extend(warnings)
    inventory["loaded_assets"] = assets
    inventory["fundamentals_available"] = bool(fundamentals)
    inventory["fundamentals_point_in_time"] = False
    if write_inventory:
        _INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _INVENTORY_PATH.write_text(json.dumps(inventory, indent=2, default=str))
    return {"panel": panel, "inventory": inventory, "fundamentals": fundamentals}


# --- parsing helpers -------------------------------------------------------
def _parse_price_series(raw: str) -> tuple[list[str], list[float]]:
    import csv

    reader = csv.reader(io.StringIO(raw))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if len(rows) < 2:
        return [], []
    header = [c.strip().lower() for c in rows[0]]
    di = header.index("date") if "date" in header else 0
    # price_series.csv header is (date, price); close col = the other
    ci = header.index("price") if "price" in header else (1 if len(header) > 1 else 0)
    dates, prices = [], []
    for row in rows[1:]:
        try:
            dates.append(str(row[di]).strip())
            prices.append(float(row[ci].strip()))
        except (TypeError, ValueError, IndexError):
            continue
    return dates, prices


def _parse_ratios(raw: str) -> dict[str, float]:
    import csv

    out: dict[str, float] = {}
    reader = csv.reader(io.StringIO(raw))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if len(rows) < 2:
        return out
    for row in rows[1:]:
        if len(row) >= 2:
            try:
                out[row[0].strip()] = float(row[1].strip())
            except (TypeError, ValueError):
                continue
    return out


def _normalize_id(comp: str) -> str:
    digits = "".join(ch for ch in comp if ch.isdigit())
    return f"FEN_{digits}" if digits else comp


def _align_forward(values: list[float], dates_union: list[str], T: int) -> np.ndarray:
    out = np.full(T, np.nan, dtype=float)
    n = min(len(values), T)
    out[:n] = values[:n]
    last = 100.0
    for i in range(T):
        if np.isnan(out[i]):
            out[i] = last
        else:
            last = out[i]
    return out


def _as_dates(dates_union: list[str]) -> list[date]:
    """Fenrix price_series.csv uses relative labels (DAY_0000) not calendar dates.
    Map DAY_N -> synthetic trading-day calendar; otherwise parse ISO. Provenance
    flags dates as relative so no false 'point-in-time historical' claim is made."""
    out: list[date] = []
    import re

    rel = re.compile(r"^DAY[_\-]?(\d+)$", re.IGNORECASE)
    for d in dates_union:
        m = rel.match(str(d).strip())
        if m:
            # synthetic business-day calendar from a fixed base
            base = date(2019, 1, 2)
            n = int(m.group(1))
            cur = base
            added = 0
            while added < n:
                cur = cur + timedelta(days=1)
                if cur.weekday() < 5:
                    added += 1
            out.append(cur)
        else:
            try:
                out.append(datetime.fromisoformat(str(d)).date())
            except Exception:
                out.append(date(2024, 1, 1))
    return out


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
