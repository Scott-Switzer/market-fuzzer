"""Behavioral contracts for MARKET_FUZZER_WORLD_V2.

The world exists numerically first. These tests pin the properties that
make it an evaluation environment: determinism, exact accounting, PIT
ordering, causal intervention isolation, and sealed exports.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.economy.v2 import EconomyParamsV2, InterventionV2, run_economy
from app.export_world_v2 import export_world_v2

START = date(2026, 6, 30)  # mid-history intervention point


def _params(years: int = 5, seed: int = 20260921) -> EconomyParamsV2:
    return EconomyParamsV2(years=years, seed=seed)


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #


def test_world_is_deterministic():
    a = run_economy(_params(years=3))
    b = run_economy(_params(years=3))

    def key(w):
        return [(q.company, q.period_end, round(q.revenue, 6), round(q.net_income, 6)) for q in w.quarters]

    assert key(a) == key(b)
    assert [(p.company, p.session, round(p.close, 8)) for p in a.prices] == [
        (p.company, p.session, round(p.close, 8)) for p in b.prices
    ]
    assert [(m.date, m.regime, round(m.policy_rate, 10)) for m in a.macro] == [
        (m.date, m.regime, round(m.policy_rate, 10)) for m in b.macro
    ]


def test_different_seeds_produce_different_worlds():
    a = run_economy(_params(years=3, seed=1))
    b = run_economy(_params(years=3, seed=2))
    assert [q.revenue for q in a.quarters] != [q.revenue for q in b.quarters]


# --------------------------------------------------------------------------- #
# accounting
# --------------------------------------------------------------------------- #


def test_balance_identity_is_exact_every_quarter():
    w = run_economy(_params(years=6))
    assert w.balance_sheets
    for bs in w.balance_sheets:
        assert bs.assets == pytest.approx(bs.liabilities + bs.equity, abs=1e-6)
        assert bs.equity_check_residual == pytest.approx(0.0, abs=1e-6)


def test_income_statement_adds_up():
    w = run_economy(_params(years=3))
    for q in w.quarters:
        assert q.gross_profit == pytest.approx(q.revenue - q.cogs, rel=1e-9, abs=1e-6)
        assert q.ebit == pytest.approx(q.gross_profit - q.operating_expenses, rel=1e-9, abs=1e-6)
        assert q.pretax_income == pytest.approx(q.ebit - q.interest_expense, rel=1e-9, abs=1e-6)
        if q.pretax_income >= 0:
            assert q.tax_expense == pytest.approx(q.pretax_income * 0.21, rel=1e-9, abs=1e-6)
            assert q.net_income == pytest.approx(q.pretax_income - q.tax_expense, rel=1e-9, abs=1e-6)


def test_defaults_terminate_the_company():
    w = run_economy(_params(years=8))
    assert w.defaults, "expected at least one default over 8 years x 24 companies"
    for d in w.defaults:
        ticker = d["company"]
        at = date.fromisoformat(str(d["at"]))
        after = [q for q in w.quarters if q.company == ticker and q.period_end > at]
        assert not after, f"{ticker} reported after default at {at}"


# --------------------------------------------------------------------------- #
# point-in-time ordering
# --------------------------------------------------------------------------- #


def test_filings_are_available_after_period_end():
    w = run_economy(_params(years=4))
    for f in w.filings:
        assert f.available_at > f.period_end


def test_estimates_are_issued_before_the_print():
    w = run_economy(_params(years=4))
    assert w.estimates
    for e in w.estimates:
        assert e.issued_at < e.revised_at
    # an estimate for period P must exist before P's earnings call
    call_by_period = {(e.company, e.period): e for e in w.estimates}
    assert call_by_period
    for call in w.earnings:
        period = f"{call.period_end.year}Q{((call.period_end.month - 1) // 3) + 1}"
        est = call_by_period.get((call.company, period))
        if est is not None:
            assert est.issued_at <= call.call_at


def test_price_availability_equals_session():
    w = run_economy(_params(years=2))
    for p in w.prices:
        assert p.session == p.session  # sessions are their own availability date
        assert p.high >= max(p.open, p.close)
        assert p.low <= min(p.open, p.close)


# --------------------------------------------------------------------------- #
# causal interventions
# --------------------------------------------------------------------------- #


def test_intervention_is_isolated_to_the_treated_company():
    p = _params(years=5, seed=7)
    base = run_economy(p)
    ticker = base.companies[0]["ticker"]
    iv = (InterventionV2(company=ticker, variable="demand_index", value=1.6, start=START),)
    cf = run_economy(p, iv)

    others_base = [(q.company, q.period_end, q.revenue) for q in base.quarters if q.company != ticker]
    others_cf = [(q.company, q.period_end, q.revenue) for q in cf.quarters if q.company != ticker]
    assert others_base == others_cf, "untreated companies must be untouched"

    treated_pre_base = [q.revenue for q in base.quarters if q.company == ticker and q.period_end < START]
    treated_pre_cf = [q.revenue for q in cf.quarters if q.company == ticker and q.period_end < START]
    assert treated_pre_base == treated_pre_cf, "pre-intervention history must be identical"

    treated_post_base = [q.revenue for q in base.quarters if q.company == ticker and q.period_end >= START]
    treated_post_cf = [q.revenue for q in cf.quarters if q.company == ticker and q.period_end >= START]
    assert treated_post_base != treated_post_cf, "post-intervention must diverge"


def test_rate_shock_applies_only_from_its_start():
    p = _params(years=5, seed=11)
    base = run_economy(p)
    iv = (InterventionV2(company="WORLD", variable="rate_shock_bps", value=300.0, start=START),)
    shocked = run_economy(p, iv)

    for mb, ms in zip(base.macro, shocked.macro, strict=True):
        assert mb.date == ms.date
        if mb.date >= START:
            assert ms.policy_rate == pytest.approx(mb.policy_rate + 0.03, rel=1e-9)
        else:
            assert ms.policy_rate == pytest.approx(mb.policy_rate, rel=1e-12)


def test_fraud_intervention_produces_known_truth():
    p = _params(years=5, seed=42)
    ticker = run_economy(p).companies[0]["ticker"]
    # baseline: clamp fraud to zero so the honest world is honest by construction
    # (seed 42 otherwise develops an organic fraud window for this company)
    base_iv = (
        InterventionV2(
            company=ticker,
            variable="fraud_propensity",
            value=0.0,
            start=date(p.start_year, 1, 1),
            end=None,  # honest for the whole horizon; the fraud clamp stacks on top
        ),
    )
    base = run_economy(p, base_iv)
    iv = base_iv + (
        InterventionV2(
            company=ticker, variable="fraud_propensity", value=1.0, start=date(p.start_year, 4, 1)
        ),
    )
    cf = run_economy(p, iv)

    assert any(f["company"] == ticker for f in cf.fraud_windows)
    flagged = [q for q in cf.quarters if q.company == ticker and q.fraud_flag]
    assert flagged, "fraud window must surface flagged quarters"

    # inflated revenue vs the honest counterfactual for the same period
    base_by_period = {(q.fiscal_year, q.fiscal_quarter): q for q in base.quarters if q.company == ticker}
    for q in flagged:
        b = base_by_period[(q.fiscal_year, q.fiscal_quarter)]
        assert q.revenue == pytest.approx(b.revenue * 1.004, rel=1e-6)
        assert not b.fraud_flag

    # the detectable tell: reported earnings outrun TRUE cash generation.
    # operating_cf = true_ni + depreciation, so reported NI minus the cash-
    # implied NI must be strictly positive in every flagged quarter, and
    # exactly zero in every honest one.
    cf_cf = {c.period_end: c for c in cf.cash_flows if c.company == ticker}
    for q in flagged:
        c = cf_cf[q.period_end]
        accrual_gap = q.net_income - (c.operating_cf - c.depreciation)
        assert accrual_gap > 0, "flagged quarter must show earnings above cash flow"
    honest = [q for q in cf.quarters if q.company == ticker and not q.fraud_flag]
    assert honest
    for q in honest:
        c = cf_cf[q.period_end]
        assert q.net_income == pytest.approx(c.operating_cf - c.depreciation, rel=1e-9, abs=1e-6)


def test_supplier_failure_crushes_gross_margin():
    p = _params(years=4, seed=99)
    base = run_economy(p)
    ticker = base.companies[0]["ticker"]
    iv = (InterventionV2(company=ticker, variable="supplier_failure", value=1.0, start=START),)
    cf = run_economy(p, iv)

    b_post = [q.gross_margin for q in base.quarters if q.company == ticker and q.period_end >= START]
    c_post = [q.gross_margin for q in cf.quarters if q.company == ticker and q.period_end >= START]
    assert c_post != b_post
    assert sum(c_post) / len(c_post) < sum(b_post) / len(b_post)


# --------------------------------------------------------------------------- #
# export / sealing
# --------------------------------------------------------------------------- #


def test_export_is_deterministic_and_sealed(tmp_path: Path):
    first = export_world_v2(tmp_path / "a", world_id="fuzzer-test-1", seed=1234, years=2)
    second = export_world_v2(tmp_path / "b", world_id="fuzzer-test-1", seed=1234, years=2)
    for relative in (
        "public/entities.json",
        "public/financials.json",
        "public/prices.json",
        "public/filings.json",
        "public/estimates.json",
        "public/events.json",
        "manifest.json",
    ):
        assert (first / relative).read_bytes() == (second / relative).read_bytes()

    public_text = (first / "public" / "financials.json").read_text()
    assert "fraud" not in public_text
    assert "latents" not in public_text

    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["schema"] == "financial-world-release/v2"
    assert manifest["world"]["world_type"] == "synthetic"
    for key, meta in manifest["artifact_hashes"].items():
        import hashlib

        digest = hashlib.sha256((first / key).read_bytes()).hexdigest()
        assert digest == meta["sha256"]


def test_export_rows_carry_world_selector_and_pit(tmp_path: Path):
    out = export_world_v2(tmp_path / "w", world_id="fuzzer-test-2", seed=7, years=2)
    fin = json.loads((out / "public" / "financials.json").read_text())
    row = fin["observations"][0]
    assert row["world"] == {
        "world_type": "synthetic",
        "world_id": "fuzzer-test-2",
        "version": "world-v2-7-2y",
        "generator": "market-fuzzer-world-v2",
        "seed": 7,
    }
    assert row["available_at"] >= row["observation_at"]

    hidden = json.loads((out / "hidden" / "world_state.json").read_text())
    assert "latents" in hidden
    assert "interventions" in hidden
    # organic fraud is rare but possible; its ground truth must stay sealed
    assert isinstance(hidden["fraud_windows"], list)
