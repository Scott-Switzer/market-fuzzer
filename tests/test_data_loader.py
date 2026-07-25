from __future__ import annotations

from pathlib import Path

import pytest
from shared import make_temp_fenrix_zip, require_fenrix_zip

from app.break_test.data_loader import (
    _DEFAULT_FENRIX_ZIP,
    _FENRIX_LOADER_CACHE,
    load_fenrix,
)


def test_fenrix_loader_default_zip_returns_company_closes() -> None:
    require_fenrix_zip()
    closes = load_fenrix()
    assert isinstance(closes, dict)
    assert closes
    for company_id, series in closes.items():
        assert company_id.startswith("COMPANY_")
        assert len(series) >= 80
        assert all(price > 0 for price in series)
        assert all(price == price for price in series)


def test_fenrix_loader_caches_by_path() -> None:
    require_fenrix_zip()
    _FENRIX_LOADER_CACHE.clear()
    first = load_fenrix()
    second = load_fenrix()
    assert second is first


def test_fenrix_loader_uses_explicit_zip() -> None:
    require_fenrix_zip()
    _FENRIX_LOADER_CACHE.clear()
    explicit = load_fenrix(_DEFAULT_FENRIX_ZIP)
    cached = load_fenrix()
    assert cached is explicit


def test_fenrix_loader_missing_zip_raises_file_not_found() -> None:
    missing = Path("/tmp/does-not-exist-fenrix-42.zip")
    if missing.exists():
        missing.unlink()
    with pytest.raises(FileNotFoundError, match="Fenrix anonymized bundle not found"):
        load_fenrix(missing)


def test_fenrix_loader_rejects_invalid_zip(tmp_path: Path) -> None:
    bad_zip = tmp_path / "bad.zip"
    bad_zip.write_text("not a zip", encoding="utf-8")
    _FENRIX_LOADER_CACHE.pop(str(bad_zip), None)
    with pytest.raises(ValueError, match="not a valid zip"):
        load_fenrix(bad_zip)


def test_fenrix_loader_rejects_empty_zip(tmp_path: Path) -> None:
    empty_zip = tmp_path / "empty.zip"
    with pytest.raises(FileNotFoundError, match="Fenrix anonymized bundle not found"):
        load_fenrix(empty_zip)


def test_fenrix_run_break_test_uses_fenrix_closes() -> None:
    require_fenrix_zip()
    from app.break_test.service import run_break_test

    closes_map = load_fenrix()
    first_company = next(iter(closes_map))
    closes = closes_map[first_company]
    result = run_break_test(
        closes,
        "sma_crossover",
        data_source="fenrix",
        lookback_period="5y",
        worlds_per_regime=10,
    )
    assert result["limitations"]["data_source"] == "fenrix"
    assert result["limitations"]["lookback_period"] == "5y"


def test_fenrix_temp_zip_is_loadable(tmp_path: Path) -> None:
    archive = make_temp_fenrix_zip(tmp_path)
    _FENRIX_LOADER_CACHE.pop(str(archive), None)
    closes = load_fenrix(archive)
    assert isinstance(closes, dict)
    assert closes
