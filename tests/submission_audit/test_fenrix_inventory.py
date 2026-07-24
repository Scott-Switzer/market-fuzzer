"""Audit tests: Fenrix bundle inventory + panel load + engine compatibility.

Runs against the real bundle at
/Users/scottthomasswitzer/Documents/scott-brain/22_Fenrix/anonymized_bundle.zip
(via fenrix_adapter's default resolution / FENRIX_DATA_PATH). Tests SKIP if the
bundle is absent so CI on other machines stays green.

Asserted inventory facts (as of the 2026-06-25 bundle):
  * 8 companies, all 8 with market/price_series.csv, metrics/daily_prices.json,
    and financials/ratio_summary.csv
  * relative DAY_NNNN dates -> synthetic calendar + provenance warning
  * fundamentals flagged NOT point-in-time
  * panel loads and the portfolio engine accepts it when >=3 assets, >=60 bars

Known bug (documented, not fixed here - app/ is read-only for this audit):
  load_panel aligns all assets to the FIRST company's date index rather than the
  union/longest (COMPANY_001 has 158 rows; others have up to 184). Longer series
  are silently truncated to 158 bars. See test_known_truncation_bug_documented.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.strategy_lab.submission.fenrix_adapter import (
    inspect_fenrix,
    load_panel,
    resolve_path,
)


def _bundle_available() -> bool:
    return resolve_path() is not None


pytestmark = pytest.mark.skipif(
    not _bundle_available(), reason="Fenrix bundle not resolvable on this machine"
)


@pytest.fixture(scope="module")
def inventory():
    return inspect_fenrix()


@pytest.fixture(scope="module")
def loaded():
    return load_panel(write_inventory=False)


def test_inventory_counts_and_fields(inventory):
    assert inventory["exists"] is True
    assert inventory.get("path_traversal_detected") is False
    assert inventory.get("total_uncompressed_bytes", 0) <= 50 * 1024 * 1024
    companies = inventory["companies"]
    assert len(companies) == 8, "bundle should contain 8 anonymized companies"
    assert sum(c["has_price_series"] for c in companies) == 8
    assert sum(c["has_ohlcv_json"] for c in companies) == 8
    assert sum(c["has_fundamentals"] for c in companies) == 8
    for c in companies:
        assert set(c) >= {"company", "has_price_series", "has_ohlcv_json", "has_fundamentals"}
    assert isinstance(inventory.get("file_sha256"), str)
    assert len(inventory["file_sha256"]) == 64


def test_panel_loads_with_all_companies(loaded):
    panel = loaded.get("panel")
    assert panel is not None, f"load_panel error: {loaded.get('error')}"
    assert panel.N == 8
    assert all(a.startswith("FEN_") for a in panel.assets)
    assert panel.T >= 150  # COMPANY_001 has 158 price rows
    assert np.all(np.isfinite(panel.close)) and np.all(panel.close > 0)
    # no benchmark exists in the bundle
    assert panel.benchmark_close is None


def test_relative_date_warning_and_synthetic_calendar(loaded):
    panel = loaded["panel"]
    # DAY_0000 maps to the fixed synthetic base date; dates strictly increase
    assert panel.dates[0].isoformat() == "2019-01-02"
    assert all(d.weekday() < 5 for d in panel.dates), "synthetic calendar is business days"
    warns = " ".join(panel.provenance.warnings) + " ".join(loaded["inventory"].get("warnings", []))
    assert "point-in-time" in warns.lower()
    assert panel.provenance.source == "fenrix"
    assert panel.provenance.tier == 1


def test_fundamentals_flagged_not_point_in_time(loaded):
    inv = loaded["inventory"]
    assert inv.get("fundamentals_available") is True
    assert inv.get("fundamentals_point_in_time") is False
    panel = loaded["panel"]
    for a in panel.assets:
        assert panel.metadata[a].point_in_time is False
    # fundamentals parsed for every company
    assert len(loaded.get("fundamentals", {})) == 8


def test_engine_runs_on_fenrix_panel(loaded):
    import dataclasses

    from app.strategy_lab.submission.engine import run_portfolio_backtest
    from app.strategy_lab.submission.strategy import CrossSectionalSpec

    panel = loaded["panel"]
    assert panel.N >= 3 and panel.T >= 60, "need enough assets/bars for an engine run"
    # shorten lookbacks so the ~158-bar panel produces signals
    spec = dataclasses.replace(
        CrossSectionalSpec(), momentum_lookback=60, momentum_short=10, volatility_window=20
    )
    result = run_portfolio_backtest(panel=panel, spec=spec, strategy_hash="fenrix-audit")
    assert result.equity_curve.shape == (panel.T,)
    assert np.all(np.isfinite(result.equity_curve))
    assert result.equity_curve[0] == pytest.approx(spec.initial_capital)
    assert len(result.trades) > 0, "short-lookback spec should trade on this panel"
    assert result.provenance is not None


def test_known_truncation_bug_documented(loaded):
    """BUG (in app/, not fixed by this read-only audit): load_panel sets
    dates_union from the FIRST company with prices instead of the union/longest
    index, so companies with more rows (up to 184) are truncated to COMPANY_001's
    158 bars. This test pins the current (buggy) behavior so a future fix makes
    it fail loudly and gets updated deliberately."""
    import csv
    import io
    import zipfile

    panel = loaded["panel"]
    target = resolve_path()
    with zipfile.ZipFile(target) as z:
        lengths = {}
        for i in range(1, 9):
            comp = f"COMPANY_{i:03d}"
            raw = z.read(f"public/anonymized/{comp}/market/price_series.csv").decode()
            rows = [r for r in csv.reader(io.StringIO(raw)) if any(c.strip() for c in r)]
            lengths[comp] = len(rows) - 1
    assert max(lengths.values()) > panel.T, (
        "panel truncated to first company's length -- if this assertion ever "
        "fails, the truncation bug was fixed; update this test"
    )
    assert panel.T == lengths["COMPANY_001"]
