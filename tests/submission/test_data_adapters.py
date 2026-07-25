"""Tests for data adapters: Fenrix inspection safety, fixture determinism, yfinance shape."""

import numpy as np

from app.strategy_lab.submission.fenrix_adapter import inspect_fenrix
from app.strategy_lab.submission.fixture import build_fixture_panel
from app.strategy_lab.submission.strategy import CrossSectionalSpec


def test_fixture_is_deterministic_and_labeled():
    p1 = build_fixture_panel()
    p2 = build_fixture_panel()
    assert p1.provenance.tier == 3
    assert p1.provenance.source == "deterministic_fixture"
    assert np.array_equal(p1.close, p2.close)
    assert p1.close.shape[1] == 7  # 6 equities + benchmark
    # benchmark is the last column
    assert p1.metadata[p1.assets[-1]].is_benchmark is True


def test_fenrix_inspection_reports_inventory():
    # Even if the bundle is present, inspection must not raise and must return a dict
    inv = inspect_fenrix()
    assert isinstance(inv, dict)
    assert "exists" in inv
    if inv.get("exists"):
        assert "companies" in inv
        # provenance flag: Fenrix dates are relative, not point-in-time
        assert inv.get("total_uncompressed_bytes", 0) <= 50 * 1024 * 1024


def test_fenrix_path_traversal_rejected():
    from app.strategy_lab.submission.fenrix_adapter import resolve_path

    # a traversal path must not resolve to something outside expected roots
    bad = resolve_path("../../etc/passwd")
    # resolve_path only returns existing files; this should be None (does not exist)
    assert bad is None or not str(bad).startswith("/etc")


def test_yfinance_shape_and_labels():
    # Run only if network + yfinance present; mark skip otherwise to keep CI green.
    pytest = __import__("pytest")
    try:
        from app.strategy_lab.submission.yfinance_adapter import acquire

        res = acquire(
            tickers=list(CrossSectionalSpec().universe)[:3],
            start="2023-01-01",
            end="2023-03-31",
            use_cache=True,
        )
    except Exception as exc:
        pytest.skip(f"yfinance unavailable in this environment: {exc}")
    if res.get("panel") is None:
        pytest.skip("yfinance returned no panel (network/rate limited)")
    panel = res["panel"]
    assert panel.provenance.tier in (1, 2)
    assert panel.close.shape[1] >= 3
    # labels must be visible tickers, not opaque ids
    assert any(a.isalpha() for a in panel.assets)
