from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

_DEFAULT_FENRIX_ZIP = Path(
    "/Users/scottthomasswitzer/Documents/scott-brain/22_Fenrix/anonymized_bundle.zip"
).expanduser()


@dataclass(frozen=True)
class FenrixLoadResult:
    loaded: bool
    path: str | None
    tickers: list[str]
    price_frames: dict[str, dict[str, Any]]
    provenance: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class FenrixHistoricalAdapter:
    @staticmethod
    def load(path: str | None = None) -> FenrixLoadResult:
        target = Path(path) if path is not None else _DEFAULT_FENRIX_ZIP
        target = target.expanduser()

        provenance: dict[str, Any] = {
            "adapter": "fenrix",
            "requested_path": str(target),
            "exists": target.exists(),
            "loaded": False,
        }

        if not target.exists():
            return FenrixLoadResult(
                loaded=False,
                path=str(target),
                tickers=[],
                price_frames={},
                provenance=provenance,
                errors=[f"Fenrix bundle not found: {target}"],
            )

        try:
            file_hash = FenrixHistoricalAdapter._file_sha256(target)
            provenance["file_size_bytes"] = target.stat().st_size
            provenance["file_sha256"] = file_hash
        except Exception as exc:
            provenance["file_hash_error"] = str(exc)

        if not zipfile.is_zipfile(target):
            provenance["zip_invalid"] = True
            return FenrixLoadResult(
                loaded=False,
                path=str(target),
                tickers=[],
                price_frames={},
                provenance=provenance,
                errors=[f"Fenrix bundle is not a valid zip: {target}"],
            )

        price_frames: dict[str, dict[str, Any]] = {}
        errors: list[str] = []

        try:
            with zipfile.ZipFile(target, mode="r") as bundle:
                for name in bundle.namelist():
                    parts = name.split("/")
                    if (
                        len(parts) >= 5
                        and parts[0] == "public"
                        and parts[1] == "anonymized"
                        and parts[3] == "market"
                        and parts[-1] == "price_series.csv"
                    ):
                        company_id = parts[2]
                        try:
                            raw = bundle.read(name).decode("utf-8", errors="ignore")
                            frame = FenrixHistoricalAdapter._parse_price_csv(raw, asset_id=company_id)
                            if frame:
                                price_frames[company_id] = frame
                        except Exception as exc:
                            errors.append(f"{name}: {exc}")
        except zipfile.BadZipFile as exc:
            provenance["zip_read_error"] = str(exc)
            return FenrixLoadResult(
                loaded=False,
                path=str(target),
                tickers=[],
                price_frames={},
                provenance=provenance,
                errors=[f"Bad zip file: {target}"],
            )

        provenance["loaded"] = bool(price_frames)
        provenance["ticker_count"] = len(price_frames)
        provenance["errors"] = errors

        return FenrixLoadResult(
            loaded=bool(price_frames),
            path=str(target),
            tickers=sorted(price_frames.keys()),
            price_frames=price_frames,
            provenance=provenance,
            errors=errors,
        )

    @staticmethod
    def _file_sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _parse_price_csv(raw: str, *, asset_id: str) -> dict[str, Any] | None:
        reader = csv.reader(io.StringIO(raw))
        rows = [row for row in reader if any(cell.strip() for cell in row)]
        if not rows:
            return None

        header = [col.strip() for col in rows[0]]
        if len(rows) < 2:
            return None

        close_idx = None
        date_idx = None
        for idx, col in enumerate(header):
            lowered = col.lower()
            if lowered == "close" and close_idx is None:
                close_idx = idx
            if lowered == "date" and date_idx is None:
                date_idx = idx
            if lowered in {"timestamp", "time", "datetime"} and date_idx is None:
                date_idx = idx

        if close_idx is None or date_idx is None:
            return None

        prices: list[float] = []
        dates: list[str] = []
        for row in rows[1:]:
            try:
                dates.append(str(row[date_idx]).strip())
                prices.append(float(row[close_idx].strip()))
            except (TypeError, ValueError):
                continue

        arr = np.asarray(prices, dtype=float)
        if arr.size == 0 or np.any(arr <= 0):
            return None

        frequency_label = FenrixHistoricalAdapter._infer_frequency(dates)
        return {
            "asset_id": asset_id,
            "dates": dates,
            "prices": arr.tolist(),
            "frequency_label": frequency_label,
            "bar_count": int(arr.size),
        }

    @staticmethod
    def _infer_frequency(dates: list[str], explicit_label: str = "inferred_daily") -> str:
        parsed = []
        for value in dates[:10]:
            try:
                parsed.append(datetime.fromisoformat(str(value).strip()))
            except Exception:
                continue
        if len(parsed) < 2:
            return explicit_label
        diffs = sorted(
            {
                (parsed[i] - parsed[i - 1]).total_seconds()
                for i in range(1, len(parsed))
                if parsed[i] > parsed[i - 1]
            }
        )
        if not diffs:
            return explicit_label
        median_seconds = diffs[len(diffs) // 2]
        if median_seconds <= 7200:
            return "inferred_hourly_or_shorter"
        if median_seconds <= 172800:
            return "inferred_daily"
        if median_seconds <= 604800:
            return "inferred_weekly"
        return explicit_label
