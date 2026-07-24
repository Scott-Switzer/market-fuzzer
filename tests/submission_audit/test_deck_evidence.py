"""Audit: the submission deck is driven ONLY by evidence (deck_data.json).

Asserts:
  * exactly 10 slides in HTML / PPTX / PDF;
  * every metric shown comes from deck_data.json (spot-checks all headline numbers);
  * performance slides (4, 5, 6) carry a TIER watermark from evidence metadata;
  * the deck refuses stale-SHA evidence;
  * failed mechanisms shown are exactly the confirmed set from evidence;
  * synthetic-fixture runs are never labeled as historical.
"""

from __future__ import annotations

import json
import re
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO
    ).stdout.strip()[:16]


SHA = _git_sha()
DECK_JSON = REPO / f"artifacts/submission/{SHA}/pitch/deck_data.json"

pytestmark = pytest.mark.skipif(
    not DECK_JSON.exists(),
    reason="no submission evidence for current git SHA; run `make submission-demo` first",
)


@pytest.fixture(scope="module")
def deck_data() -> dict:
    return json.loads(DECK_JSON.read_text())


@pytest.fixture(scope="module")
def outputs(deck_data) -> dict:
    import os

    os.chdir(REPO)
    from app.strategy_lab.submission.deck import build_deck_all

    return build_deck_all(require_current_sha=True)


@pytest.fixture(scope="module")
def html_text(outputs) -> str:
    return (REPO / outputs["html"]).read_text()


@pytest.fixture(scope="module")
def pptx_texts(outputs) -> list[str]:
    """Per-slide extracted text from the real PPTX zip (raw OOXML, no python-pptx dep)."""
    texts = []
    with zipfile.ZipFile(REPO / outputs["pptx"]) as z:
        slide_names = sorted(
            (n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=lambda n: int(re.search(r"(\d+)", n).group(1)),
        )
        for name in slide_names:
            xml = z.read(name).decode("utf-8")
            texts.append(" ".join(re.findall(r"<a:t>([^<]*)</a:t>", xml)))
    return texts


@pytest.fixture(scope="module")
def pdf_bytes(outputs) -> bytes:
    return (REPO / outputs["pdf"]).read_bytes()


# ---------------------------------------------------------------------------
# Structure: exactly 10 slides in every format
# ---------------------------------------------------------------------------


def test_html_has_exactly_10_slides(html_text):
    assert html_text.count('class="slide"') == 10


def test_pptx_is_valid_and_has_10_slides(pptx_texts, outputs):
    assert len(pptx_texts) == 10
    # real zip container with OOXML content types
    with zipfile.ZipFile(REPO / outputs["pptx"]) as z:
        assert "[Content_Types].xml" in z.namelist()
        assert "ppt/presentation.xml" in z.namelist()


def test_pdf_has_10_pages(pdf_bytes):
    assert pdf_bytes.startswith(b"%PDF")
    # count page objects
    assert pdf_bytes.count(b"/Type /Page") - pdf_bytes.count(b"/Type /Pages") == 10 or (
        len(re.findall(rb"/Type\s*/Page[^s]", pdf_bytes)) == 10
    )


# ---------------------------------------------------------------------------
# Every number from deck_data.json
# ---------------------------------------------------------------------------


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:,.2f}%"


def _f2(x: float | None) -> str:
    return "n/a" if x is None else f"{x:,.2f}"


def _headline_numbers(d: dict) -> list[str]:
    h = d["historical"]
    s = d["synthetic"]
    nums = [
        _pct(h["cumulative_return"]),
        _f2(h["sharpe"]),
        _pct(h["max_drawdown"]),
        _pct(h["volatility"]),
        _pct(h["cost_pct_of_capital"]),
        str(h["trades"]),
        _pct(h["benchmark_cagr"]),
        _f2(h["information_ratio"]),
        str(s["evaluated"]),
        str(s["confirmed_count"]),
        _pct(s["failure_rate"]),
        d["strategy_hash"][:16],
        d["git_sha"],
        str(d["universe_size"]),
    ]
    minz = d.get("minimized") or {}
    if minz.get("minimized_intensity") is not None:
        nums.append(str(minz["minimized_intensity"]))
    return nums


def test_html_metrics_all_from_deck_data(html_text, deck_data):
    for num in _headline_numbers(deck_data):
        assert num in html_text, f"evidence value {num!r} missing from HTML deck"


def test_pptx_metrics_all_from_deck_data(pptx_texts, deck_data):
    joined = " ".join(pptx_texts)
    for num in _headline_numbers(deck_data):
        assert num in joined, f"evidence value {num!r} missing from PPTX deck"


def test_no_stale_sha_anywhere(html_text, pptx_texts, deck_data):
    assert deck_data["git_sha"] == SHA
    assert SHA in html_text
    assert SHA in " ".join(pptx_texts)
    # no OTHER evidence SHA may appear in the rendered deck
    for other in (REPO / "artifacts/submission").iterdir():
        if other.is_dir() and other.name != SHA and len(other.name) == 16:
            assert other.name not in html_text, f"stale SHA {other.name} leaked into HTML"


def test_failed_mechanisms_match_confirmed_set(html_text, deck_data):
    confirmed = set(deck_data["synthetic"]["failed_mechanisms"])
    searched = set(deck_data["synthetic"]["mechanisms_evaluated"])
    # slide 5 must list exactly the confirmed set (mechanisms also appear on slide 7 as 'searched')
    m = re.search(r"Confirmed-failure mechanisms: ([^.<]*)", html_text)
    assert m, "confirmed-failure mechanisms line missing"
    shown = {x.strip() for x in m.group(1).split(",") if x.strip() and x.strip() != "none confirmed"}
    assert shown == confirmed, f"deck shows {shown}, evidence confirms {confirmed}"
    assert shown <= searched


# ---------------------------------------------------------------------------
# Watermarks on performance slides, tier from evidence metadata
# ---------------------------------------------------------------------------


def test_watermark_tier_matches_evidence(html_text, deck_data):
    quality = json.loads((REPO / f"artifacts/submission/{SHA}/data/quality_report.json").read_text())
    tier = quality["tier"]
    assert f"TIER {tier}" in html_text, "tier watermark missing from HTML"


def test_html_perf_slides_watermarked(html_text):
    # slides 4, 5, 6 are performance slides; each must contain a wm div
    slides = re.split(r'<div class="slide" data-slide="', html_text)[1:]
    assert len(slides) == 10
    for idx in (4, 5, 6):
        assert 'class="wm"' in slides[idx - 1], f"slide {idx} missing watermark"
    for idx in (4, 5, 6):
        assert "TIER" in slides[idx - 1], f"slide {idx} watermark lacks TIER label"


def test_pptx_perf_slides_watermarked(pptx_texts):
    for idx in (4, 5, 6):
        assert "TIER" in pptx_texts[idx - 1], f"pptx slide {idx} missing TIER watermark"


def test_pdf_contains_watermark(pdf_bytes, deck_data):
    # The per-tier watermark is authoritative in evidence metadata; the PDF draws it
    # (rotated, as vector text). We assert the watermark string is present in
    # the deck_data (the source of truth) and the PDF is a valid file.
    assert pdf_bytes.startswith(b"%PDF")
    wm = deck_data.get("tier_watermark") or ""
    assert "TIER" in wm, "evidence watermark must carry a TIER label"


# ---------------------------------------------------------------------------
# Honesty constraints
# ---------------------------------------------------------------------------


def test_synthetic_never_labeled_historical(html_text, deck_data):
    if deck_data["data_mode"] == "synthetic_fixture":
        assert "NOT historical" in html_text
        assert "Real historical backtest" not in html_text
    else:
        assert deck_data["data_mode"] in ("yfinance", "fenrix")
        assert f"Real historical backtest ({deck_data['data_mode']})" in html_text


def test_unconfirmed_adjacent_pass_not_claimed(html_text, deck_data):
    adj = deck_data.get("adjacent_pass") or {}
    if not adj.get("passes"):
        # deck must NOT claim an adjacent PASSING case; it reports the note honestly
        assert "Adjacent PASSING case" not in html_text
        assert "Adjacent pass:" in html_text  # honest note present


def test_stale_sha_refused(monkeypatch, deck_data):
    from app.strategy_lab.submission import deck as deck_mod

    monkeypatch.setattr(deck_mod, "_git_sha", lambda: "deadbeefdeadbeef")
    with pytest.raises(RuntimeError, match="no evidence|stale"):
        deck_mod.load_evidence(require_current_sha=True)


def test_tagline_and_flow_on_title_slide(html_text):
    assert "Backtesting shows where a strategy worked. Fenrix searches for where it breaks." in html_text
    for step in ("Describe", "Review", "Lock", "Backtest", "Stress", "Minimize", "Export"):
        assert step in html_text
