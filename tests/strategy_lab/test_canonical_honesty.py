"""Honesty guard tests (Phase 2.5 section 13.6).

The authoritative product path (v2 router, canonical services, authoritative UI)
must contain no hard-coded substitution: no guarded_pov / arena_policy /
deterministic_product_fixture / fabricated failure. These tests are SCOPED to the
authoritative surfaces; quarantined legacy modules may still contain the strings.
"""

from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]

# Authoritative surfaces only (NOT the whole repo — legacy is quarantined).
AUTHORITATIVE_FILES = [
    REPO / "app/strategy_lab/canonical/router.py",
    REPO / "app/strategy_lab/canonical/compilation_service.py",
    REPO / "app/strategy_lab/canonical/approval_service.py",
    REPO / "app/strategy_lab/canonical/backtest_service.py",
    REPO / "app/strategy_lab/canonical/campaign_service.py",
    REPO / "app/strategy_lab/canonical/data_service.py",
    REPO / "app/strategy_lab/canonical/evidence_service.py",
    REPO / "app/static/strategy-lab.html",
]

FORBIDDEN = [
    "guarded_pov",
    "arena_policy",
    "deterministic_product_fixture",
    "trend_reversal",
    "StrategyPlanner",
    "ApprovalService",
    "aggressive_pov",
]


def test_authoritative_surfaces_have_no_legacy_substitution() -> None:
    for path in AUTHORITATIVE_FILES:
        text = path.read_text()
        for token in FORBIDDEN:
            assert token not in text, f"forbidden token {token!r} found in authoritative file {path.name}"


def test_ui_uses_only_v2_api() -> None:
    html = (REPO / "app/static/strategy-lab.html").read_text()
    # The authoritative UI must call the v2 API and not the legacy endpoints.
    assert "/api/strategy-lab/v2/" in html
    assert "/api/strategy-lab/compile" not in html
    assert "/api/strategy-lab/backtests" not in html
    assert "/api/strategy-lab/sealed/run" not in html


def test_ui_declares_replay_is_not_orderbook() -> None:
    html = (REPO / "app/static/strategy-lab.html").read_text()
    assert "not an order-book or live-market replay" in html.lower()
    # No fabricated exchange claims.
    for token in ["matching-engine", "order-book event", "queue position", "latency fill"]:
        assert token not in html.lower(), f"exchange claim {token!r} must not appear in UI"


def test_no_exchange_replay_language_in_services() -> None:
    for path in AUTHORITATIVE_FILES:
        if path.suffix == ".py":
            text = path.read_text().lower()
            assert "exchange replay" not in text
            assert "order-book replay" not in text
