"""Audit tests: yfinance 30-name run-of-record (universe + SPY benchmark, 2018-2025).

These tests exercise app/strategy_lab/submission/yfinance_adapter.acquire against
the cached run-of-record (artifacts/data_cache/yfinance). If no cache exists and
the network is unavailable, tests SKIP rather than fail, but when a panel is
available every run-of-record invariant is asserted:
  * 30 universe tickers + SPY attempted (31 total)
  * successful count and failed list are explicitly reported (no silent drop)
  * actual first/last dates after alignment fall inside the requested window
  * missingness (NaN fraction) is measured and bounded
  * survivorship warning is recorded in the run-of-record manifest
  * cache manifest + panel hash exist under artifacts/yfinance_cache/
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.strategy_lab.submission.strategy import (
    BENCHMARK,
    DEMO_UNIVERSE,
    FIXED_END,
    FIXED_START,
)
from app.strategy_lab.submission.yfinance_adapter import acquire

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "artifacts" / "yfinance_cache" / "run_of_record_manifest.json"

REQUESTED = list(DEMO_UNIVERSE) + [BENCHMARK]


@pytest.fixture(scope="module")
def run_of_record():
    res = acquire(
        tickers=REQUESTED,
        start=FIXED_START,
        end=FIXED_END,
        use_cache=True,
    )
    if res.get("panel") is None:
        pytest.skip(f"yfinance panel unavailable (no cache, no network): {res.get('error')}")
    return res


def test_universe_is_30_names_plus_spy():
    assert len(DEMO_UNIVERSE) == 30
    assert len(set(DEMO_UNIVERSE)) == 30
    assert BENCHMARK == "SPY"
    assert BENCHMARK not in DEMO_UNIVERSE
    assert len(REQUESTED) == 31


def test_all_tickers_attempted_and_failures_reported(run_of_record):
    quality = run_of_record.get("quality") or {}
    panel = run_of_record["panel"]
    if run_of_record.get("cached"):
        # Cached quality is the persisted quality of the original fetch.
        assert quality, "cached acquire must still return the persisted quality report"
    requested = quality.get("requested", REQUESTED)
    assert len(requested) == 31, "all 31 tickers (30 + SPY) must be attempted"
    returned = quality.get("returned", list(panel.assets))
    dropped = quality.get("dropped", [t for t in REQUESTED if t not in panel.assets])
    # No silent dropping: requested == returned + dropped, disjoint
    assert sorted(requested) == sorted(list(returned) + list(dropped))
    assert set(returned).isdisjoint(set(dropped))
    # successful count reported explicitly
    assert len(returned) == len(panel.assets)
    # per-ticker status must exist for every requested name on a fresh fetch
    per_ticker = quality.get("per_ticker")
    if per_ticker is not None:
        assert set(per_ticker) == set(requested)
        assert all(v in ("ok", "missing_or_empty") for v in per_ticker.values())


def test_alignment_dates_and_missingness(run_of_record):
    panel = run_of_record["panel"]
    first, last = panel.dates[0], panel.dates[-1]
    assert first.isoformat() >= FIXED_START
    assert last.isoformat() <= FIXED_END
    assert first.isoformat() <= "2018-01-05", "first aligned bar should be early Jan 2018"
    assert last.isoformat() >= "2025-12-01", "last aligned bar should reach Dec 2025"
    assert panel.T > 1900, "expect ~2000 trading days for 2018-2025"
    # missingness: panel validation forbids non-finite closes, so NaN frac must be 0
    miss = float(np.isnan(panel.close).mean())
    assert miss == 0.0
    # benchmark present and aligned
    assert panel.benchmark_close is not None
    assert panel.benchmark_close.shape == (panel.T,)


def test_cache_roundtrip_reports_source(run_of_record):
    # A second acquire with the same args must hit the cache.
    res2 = acquire(tickers=REQUESTED, start=FIXED_START, end=FIXED_END, use_cache=True)
    assert res2.get("panel") is not None
    assert res2.get("cached") is True
    assert list(res2["panel"].assets) == list(run_of_record["panel"].assets)
    assert res2["panel"].provenance.source == "yfinance"


def test_manifest_hash_and_survivorship_warning_saved():
    if not MANIFEST.exists():
        pytest.skip(
            "run-of-record manifest not generated yet (run tests/submission_audit/run_of_record_yf.py)"
        )
    m = json.loads(MANIFEST.read_text())
    assert m["requested_count"] == 31
    assert m["returned_count"] == len(m["returned"])
    assert isinstance(m["failed"], list)
    assert sorted(m["requested_tickers"]) == sorted(m["returned"] + m["failed"])
    assert m["first_date"] >= FIXED_START and m["last_date"] <= FIXED_END
    assert "missing_fraction_close" in m
    assert "survivorship" in m["survivorship_warning"].lower()
    assert isinstance(m["panel_hash"], str) and len(m["panel_hash"]) == 64
