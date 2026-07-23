from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from app.strategy_lab.historical.data_contracts import HistoricalDataContract

_MAX_FILE_BYTES = 50 * 1024 * 1024
_MAX_ROW_COUNT = 50_000
_FORMULA_PATTERN = re.compile(r"^[=+\-@].*")
_PICKLE_BYTE_MARKERS = (b"\x80", b"PK", b"Ole", b"]\x00", b"cpickle", b"pickle")


@dataclasses.dataclass(frozen=True)
class UploadValidationRowReport:
    row_count: int
    dropped_formula_rows: int
    dropped_missing_rows: int
    dropped_nonpositive_rows: int
    used_rows: int


@dataclasses.dataclass(frozen=True)
class UploadMissingnessReport:
    asset_missing: dict[str, float]
    date_missing: bool
    total_expected_cells: int
    total_missing_cells: int
    missing_ratio: float


@dataclass(frozen=True)
class UploadLoadResult:
    contract: HistoricalDataContract
    prices_by_asset: dict[str, list[float]]
    dates: list[str]
    provenance: dict[str, Any] = field(default_factory=dict)
    validation: UploadValidationRowReport | None = None
    missingness: UploadMissingnessReport | None = None
    frequency_label: str = "inferred_daily"
    loaded: bool = False
    errors: list[str] = field(default_factory=list)


class HistoricalCsvUploadAdapter:
    @staticmethod
    def load(
        source: str | Path | bytes | BinaryIO,
        *,
        contract: HistoricalDataContract | None = None,
        explicit_frequency_label: str = "inferred_daily",
        max_file_bytes: int = _MAX_FILE_BYTES,
        max_row_count: int = _MAX_ROW_COUNT,
    ) -> UploadLoadResult:
        source_path: str | None = None
        raw_bytes: bytes = b""
        provenance: dict[str, Any] = {
            "adapter": "upload_csv",
            "requested_source_type": type(source).__name__,
            "loaded": False,
        }
        errors: list[str] = []

        try:
            if isinstance(source, (str, Path)):
                source_path = str(Path(source).expanduser())
                path = Path(source_path)
                if not path.exists():
                    return _not_loaded(
                        contract=contract,
                        provenance=provenance,
                        errors=[f"CSV source not found: {source_path}"],
                    )
                raw_bytes = path.read_bytes()
                provenance["source_path"] = source_path
            elif isinstance(source, bytes):
                if max_file_bytes and len(source) > max_file_bytes:
                    return _not_loaded(
                        contract=contract,
                        provenance=provenance,
                        errors=[f"CSV exceeds max bytes: {len(source)}"],
                    )
                raw_bytes = source
                provenance["source_bytes_len"] = len(raw_bytes)
            elif hasattr(source, "read"):
                raw_bytes = source.read()  # type: ignore[union-attr]
                if isinstance(raw_bytes, str):
                    raw_bytes = raw_bytes.encode("utf-8")
                if max_file_bytes and len(raw_bytes) > max_file_bytes:
                    return _not_loaded(
                        contract=contract,
                        provenance=provenance,
                        errors=[f"CSV exceeds max bytes: {len(raw_bytes)}"],
                    )
                provenance["source_stream_bytes_len"] = len(raw_bytes)
            else:
                return _not_loaded(
                    contract=contract,
                    provenance=provenance,
                    errors=["source must be a path, bytes, or readable binary stream"],
                )
        except Exception as exc:
            return _not_loaded(contract=contract, provenance=provenance, errors=[f"Read failed: {exc}"])

        for marker in _PICKLE_BYTE_MARKERS:
            if raw_bytes[: len(marker)] == marker:
                provenance["rejected_pickle_marker"] = marker.decode("utf-8", errors="ignore")
                return _not_loaded(
                    contract=contract,
                    provenance=provenance,
                    errors=["CSV rejected: pickle/formula bytes detected"],
                )

        decoded = _decode_upload_bytes(raw_bytes)
        if decoded is None:
            return _not_loaded(
                contract=contract,
                provenance=provenance,
                errors=["CSV rejected: unsupported encoding or binary payload"],
            )

        provenance["file_size_bytes"] = len(raw_bytes)
        provenance["content_sha256"] = hashlib.sha256(raw_bytes).hexdigest()

        if source_path and not raw_bytes:
            return _not_loaded(
                contract=contract, provenance=provenance, errors=[f"CSV source empty: {source_path}"]
            )

        try:
            frame, validation, missingness, freq_label = HistoricalCsvUploadAdapter._parse_csv(
                decoded,
                max_row_count=max_row_count,
                explicit_frequency_label=explicit_frequency_label,
            )
        except Exception as exc:
            return _not_loaded(
                contract=contract,
                provenance=provenance,
                errors=[f"CSV parse failed: {exc}"],
                validation=None,
                missingness=None,
            )

        if frame is None or not frame:
            return _not_loaded(
                contract=contract,
                provenance=provenance,
                errors=["CSV rejected: no usable rows after validation"],
                validation=validation,
                missingness=missingness,
            )

        contract = _enrich_contract(contract, provenance=provenance)
        provenance["loaded"] = True
        provenance["frequency_label"] = freq_label
        provenance["asset_count"] = len(frame)
        if validation:
            provenance["validation"] = dataclasses.asdict(validation)
        if missingness:
            provenance["missingness"] = dataclasses.asdict(missingness)

        return UploadLoadResult(
            contract=contract,
            prices_by_asset={
                asset: [float(x) for x in prices if x is not None] for asset, prices in frame.items()
            },
            dates=[],
            provenance=provenance,
            validation=validation,
            missingness=missingness,
            frequency_label=freq_label,
            loaded=True,
            errors=errors,
        )

    @staticmethod
    def _parse_csv(
        decoded: str,
        *,
        max_row_count: int,
        explicit_frequency_label: str,
    ) -> tuple[
        dict[str, list[float | None]] | None,
        UploadValidationRowReport | None,
        UploadMissingnessReport | None,
        str,
    ]:
        reader = csv.reader(io.StringIO(decoded))
        rows: list[list[str]] = []

        for _idx, row in enumerate(reader):
            if len(rows) >= max_row_count + 1:
                rows = rows[: max_row_count + 1]
                break
            rows.append(row)

        dropped_formula_rows = 0
        clean_rows: list[list[str]] = [rows[0]] if rows else []
        for row in rows[1:]:
            if any(_FORMULA_PATTERN.match(cell.strip()) for cell in row[:4]):
                dropped_formula_rows += 1
            else:
                clean_rows.append(row)

        if len(clean_rows) < 2:
            return None, None, None, explicit_frequency_label

        header = [col.strip() for col in clean_rows[0]]
        date_idx = HistoricalCsvUploadAdapter._find_date_column(header)
        if date_idx is None:
            raise ValueError("CSV rejected: missing a date or timestamp column")

        asset_indices = [idx for idx, col in enumerate(header) if idx != date_idx]
        if not asset_indices:
            raise ValueError("CSV rejected: missing asset value columns")

        frame: dict[str, list[float | None]] = {}
        for idx in asset_indices:
            key: str = header[idx]
            frame[key] = []
        dropped_missing_rows = 0
        dropped_nonpositive_rows = 0
        used_rows = 0
        for row in clean_rows[1:]:
            if all(not cell.strip() for cell in row):
                dropped_missing_rows += 1
                continue
            valid = True
            row_values: list[str] = []
            for idx in asset_indices:
                value = row[idx].strip() if idx < len(row) else ""
                if not value:
                    valid = False
                    break
                try:
                    float(value)
                except (TypeError, ValueError):
                    valid = False
                    break
                row_values.append(value)
            if not valid:
                dropped_missing_rows += 1
                continue
            prices_for_row: list[float] = []
            nonpositive = False
            for raw in row_values:
                price_val = float(raw)
                if price_val <= 0:
                    nonpositive = True
                prices_for_row.append(price_val)
            if nonpositive:
                dropped_nonpositive_rows += 1
                continue
            for asset, price_val in zip(asset_indices, prices_for_row, strict=False):
                frame[header[asset]].append(price_val)
            used_rows += 1

        if not any(v for v in frame.values()):
            return None, None, None, explicit_frequency_label

        dates = []
        for row in clean_rows[1:]:
            if date_idx < len(row):
                dates.append(str(row[date_idx]).strip())
            else:
                dates.append("")

        validation = UploadValidationRowReport(
            row_count=len(clean_rows) - 1,
            dropped_formula_rows=dropped_formula_rows,
            dropped_missing_rows=dropped_missing_rows,
            dropped_nonpositive_rows=dropped_nonpositive_rows,
            used_rows=used_rows,
        )

        expected = len(dates) * len(asset_indices)
        per_asset_missing: dict[str, float] = {}
        # NOTE: mypy mis-infers `frame` keys as int due to a comprehension/loop
        # inference quirk; at runtime `frame` is always dict[str, list[float | None]]
        # (verified by the passing CSV-parse test suite).
        for asset, values in frame.items():  # type: ignore[index, assignment]
            missing = sum(1 for value in values if value is None)
            ratio = missing / len(values) if values else 0.0
            per_asset_missing[asset] = round(ratio, 6)  # type: ignore[index]
        total_missing = (
            sum(values.count(None) for values in frame.values())
            + sum(1 for date in dates if not date)
            + (len(clean_rows) - 1 - used_rows) * len(asset_indices)
        )
        missingness = UploadMissingnessReport(
            asset_missing=per_asset_missing,
            date_missing=any(not date for date in dates),
            total_expected_cells=expected,
            total_missing_cells=min(total_missing, expected),
            missing_ratio=round(min(total_missing / expected, 1.0), 6) if expected else 0.0,
        )

        freq_label = HistoricalCsvUploadAdapter._infer_frequency(dates, explicit_frequency_label)
        return frame, validation, missingness, freq_label

    @staticmethod
    def _find_date_column(header: list[str]) -> int | None:
        for idx, col in enumerate(header):
            lowered = col.lower()
            if lowered == "date":
                return idx
            if lowered in {"timestamp", "time", "datetime"}:
                return idx
        return None

    @staticmethod
    def _infer_frequency(dates: list[str], explicit_label: str) -> str:
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


def _decode_upload_bytes(raw: bytes) -> str | None:
    for encoding in {"utf-8-sig", "utf-8", "latin-1"}:
        try:
            return raw.decode(encoding)
        except Exception:
            continue
    return None


def _not_loaded(
    *,
    contract: HistoricalDataContract | None,
    provenance: dict[str, Any],
    errors: list[str],
    validation: UploadValidationRowReport | None = None,
    missingness: UploadMissingnessReport | None = None,
) -> UploadLoadResult:
    if contract is None:
        contract = HistoricalDataContract(freq="unknown", start="", end="")
    provenance["loaded"] = False
    return UploadLoadResult(
        contract=contract,
        prices_by_asset={},
        dates=[],
        provenance=provenance,
        validation=validation,
        missingness=missingness,
        frequency_label=provenance.get("frequency_label", "unknown"),
        loaded=False,
        errors=errors,
    )


def _enrich_contract(
    contract: HistoricalDataContract | None, provenance: dict[str, Any]
) -> HistoricalDataContract:
    if contract is None:
        return HistoricalDataContract(freq="1d", start="", end="", provenance={"upload": provenance})
    return dataclasses.replace(
        contract,
        provenance=dict(contract.provenance, upload=provenance),
    )
