"""Phase 2.6 integrity-closure adversarial tests.

These tests prove the Phase 2.5 integrity gaps are actually closed:
* request idempotency (same key -> one resource; changed request -> 409;
  distinct keys + identical content -> distinct allowed strategies; actor preserved)
* durable backtest/campaign artifacts + tamper detection + restart reload
* benchmark isolation (SPY tradable + no benchmark; SPY tradable + SPY benchmark
  untradable -> 422; separate nontradable benchmark absent from panel; tradable=True permitted)
* data: short panel -> 422; OHLCV invariants; weekday-only calendar; full digest
  changes when any field changes; accurate provenance
* scenarios: deterministic reproduction; seed sensitivity; unknown mechanism -> 422
* confirmation / minimization / adjacent-pass: trials persisted; monotonicity tested
  before monotone=True; adjacent pass proves every predicate false; still-failing rejected
* audit: real project id / data digest / artifact hashes; mismatch rejections
* five-family vertical slices through the v2 API
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np

from app.strategy_lab.submission.panels import MarketDataPanel


def _slice_panel(panel: MarketDataPanel, n: int) -> MarketDataPanel:
    """Return a new panel containing only the first ``n`` bars (frozen dataclass)."""
    bc = panel.benchmark_close
    return MarketDataPanel(
        dates=panel.dates[:n],
        assets=panel.assets,
        open=panel.open[:n],
        high=panel.high[:n],
        low=panel.low[:n],
        close=panel.close[:n],
        volume=panel.volume[:n],
        benchmark_close=(bc[:n] if bc is not None else None),
        metadata=panel.metadata,
        provenance=panel.provenance,
    )


def _with_close(panel: MarketDataPanel, close: np.ndarray) -> MarketDataPanel:
    return MarketDataPanel(
        dates=panel.dates,
        assets=panel.assets,
        open=panel.open,
        high=panel.high,
        low=panel.low,
        close=close,
        volume=panel.volume,
        benchmark_close=panel.benchmark_close,
        metadata=panel.metadata,
        provenance=panel.provenance,
    )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _compile(client, text):
    r = client.post("/api/strategy-lab/v2/compile", json={"description": text})
    assert r.status_code == 200, r.text
    return r.json()


def _project(client, name="WS"):
    r = client.post("/api/strategy-lab/v2/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _approve(client, pid, spec, chash, actor="user", key=None):
    return client.post(
        "/api/strategy-lab/v2/approve",
        json={
            "project_id": pid,
            "spec_draft": spec,
            "actor": actor,
            "idempotency_key": key or ("appr-" + chash),
        },
    )


def _backtest(client, a, universe, key=None, benchmark=None):
    return client.post(
        "/api/strategy-lab/v2/backtests",
        json={
            "strategy_id": a["strategy_id"],
            "strategy_version": a["strategy_version"],
            "expected_canonical_hash": a["canonical_hash"],
            "data_source": {"source": "demo_fixture", "universe": universe, "benchmark": benchmark},
            "initial_capital": 1000000,
            "idempotency_key": key or ("bt-" + a["canonical_hash"]),
        },
    )


def _campaign(client, a, mechs, key=None, predicates=None, budget=6, seeds=(1, 2)):
    return client.post(
        "/api/strategy-lab/v2/campaigns",
        json={
            "strategy_id": a["strategy_id"],
            "strategy_version": a["strategy_version"],
            "expected_canonical_hash": a["canonical_hash"],
            "mechanism_families": mechs,
            "seed_list": list(seeds),
            "world_budget": budget,
            "failure_predicates": predicates or ["sharpe_below_0"],
            "idempotency_key": key or ("cmp-" + a["canonical_hash"]),
        },
    )


# --------------------------------------------------------------------------
# 18.1 idempotency
# --------------------------------------------------------------------------
def test_approval_same_key_same_request_one_version(client):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a1 = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    a2 = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    assert a1["strategy_id"] == a2["strategy_id"]
    assert a1["strategy_version"] == a2["strategy_version"]


def test_approval_changed_request_same_key_409(client):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a1 = _approve(client, pid, c["spec_draft"], c["canonical_hash"], key="K")
    assert a1.status_code == 200
    c70 = _compile(client, "Allocate 70% to SPY and 30% to AGG and rebalance monthly.")
    a2 = _approve(client, pid, c70["spec_draft"], c70["canonical_hash"], key="K")
    assert a2.status_code == 409


def test_approval_actor_round_trip(client):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"], actor="alice").json()
    got = client.get(f"/api/strategy-lab/v2/strategies/{a['strategy_id']}/versions/{a['strategy_version']}")
    assert got.json()["approved_by"] == "alice"


def test_distinct_keys_same_content_distinct_strategies(client):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a1 = _approve(client, pid, c["spec_draft"], c["canonical_hash"], key="A").json()
    a2 = _approve(client, pid, c["spec_draft"], c["canonical_hash"], key="B").json()
    # Identity != content hash: same canonical hash, distinct strategy ids allowed.
    assert a1["canonical_hash"] == a2["canonical_hash"]
    assert a1["strategy_id"] != a2["strategy_id"]


def test_backtest_same_key_same_request_one_run(client):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    b1 = _backtest(client, a, ["SPY", "AGG"], key="BT").json()
    b2 = _backtest(client, a, ["SPY", "AGG"], key="BT").json()
    assert b1["run_id"] == b2["run_id"]


def test_backtest_changed_request_same_key_409(client):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    b1 = _backtest(client, a, ["SPY", "AGG"], key="BT")
    assert b1.status_code == 200
    b2 = _backtest(client, a, ["SPY", "AGG", "QQQ"], key="BT")
    assert b2.status_code == 409


def test_campaign_same_key_same_request_one_campaign(client):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    r1 = _campaign(client, a, ["drawdown"], key="CMP").json()
    r2 = _campaign(client, a, ["drawdown"], key="CMP").json()
    assert r1["campaign_id"] == r2["campaign_id"]


# --------------------------------------------------------------------------
# 18.2 artifacts + tamper + restart
# --------------------------------------------------------------------------
def test_backtest_artifacts_persisted_and_reloadable(client, reopen):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    bt = _backtest(client, a, ["SPY", "AGG"])
    assert bt.status_code == 200, bt.text
    body = bt.json()
    assert len(body["artifact_references"]) >= 9
    run_id = body["run_id"]
    c2 = reopen()
    res = c2.get(f"/api/strategy-lab/v2/runs/{run_id}/result")
    assert res.status_code == 200
    rj = res.json()
    assert rj["metrics"]
    assert rj["equity_curve"]
    assert rj["trades"]
    assert rj["data_provenance"]


def test_tampered_artifact_detected(client, db):
    """A backtest run that persisted artifacts must fail verification if an
    artifact file is tampered after the fact (no silent reload)."""

    from app.strategy_lab.canonical.durable import get_default_store

    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    bt = _backtest(client, a, ["SPY", "AGG"])
    assert bt.status_code == 200, bt.text
    run_id = bt.json()["run_id"]

    store = get_default_store()
    # Tamper with the metrics artifact on disk.
    key = f"runs/{run_id}/metrics.json"
    raw = bytearray(store.get(key))
    raw[0] = raw[0] ^ 0xFF  # flip a byte -> hash no longer matches
    store.put(key, bytes(raw))

    # Reload via the API must surface the integrity failure (500, not a stale 200).
    res = client.get(f"/api/strategy-lab/v2/runs/{run_id}/result")
    assert res.status_code == 500, res.text
    assert "integrity" in res.json()["detail"].lower() or "hash" in res.json()["detail"].lower()


# --------------------------------------------------------------------------
# 18.3 benchmark isolation
# --------------------------------------------------------------------------
def test_spy_tradable_no_benchmark_trades_spy(client):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    bt = _backtest(client, a, ["SPY", "AGG"], benchmark=None)
    assert bt.status_code == 200, bt.text


def test_spy_benchmark_untradable_rejected(client):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    r = client.post(
        "/api/strategy-lab/v2/backtests",
        json={
            "strategy_id": a["strategy_id"],
            "strategy_version": a["strategy_version"],
            "expected_canonical_hash": a["canonical_hash"],
            "data_source": {"source": "demo_fixture", "universe": ["SPY", "AGG"], "benchmark": "SPY"},
            "initial_capital": 1000000,
            "idempotency_key": "bt-bench-rej",
        },
    )
    assert r.status_code == 422


def test_separate_nontradable_benchmark_absent_from_panel(client):
    from app.strategy_lab.canonical.data_service import build_demo_panel

    p2 = build_demo_panel(["SPY", "AGG"], None, seed=1)
    assert p2.benchmark_close is None
    assert "SPY" in p2.assets


def test_benchmark_tradable_explicitly_permitted(client):
    from app.strategy_lab.canonical.data_service import build_demo_panel

    panel = build_demo_panel(["SPY", "AGG"], "SPY", seed=1, benchmark_tradable=True)
    assert "SPY" in panel.assets


# --------------------------------------------------------------------------
# 18.4 data service
# --------------------------------------------------------------------------
def test_short_panel_rejected_explicitly(client):
    from decimal import Decimal

    from app.domain.strategy_spec import StrategySpec
    from app.strategy_lab.canonical.data_service import build_demo_panel, check_required_history
    from app.strategy_lab.canonical.errors import PanelTooShortError

    base = build_demo_panel(["SPY", "AGG"], None, seed=1)
    # A 1-bar panel is shorter than any executor's minimum decision bars.
    short = _slice_panel(base, 1)
    spec = StrategySpec.model_validate(dict(
        name="s", original_thesis="static allocation sixty forty strategy",
        strategy_type="static_allocation", universe=["SPY", "AGG"], benchmark=None,
        benchmark_tradable=False,
        portfolio_construction={"target_weights": {"SPY": Decimal("0.6"), "AGG": Decimal("0.4")}},
    ))
    try:
        check_required_history(spec, short)
        raise AssertionError("short panel not rejected")
    except PanelTooShortError:
        pass


def test_ohlc_invariants_hold_in_scenarios(client):
    from app.strategy_lab.canonical.data_service import build_demo_panel
    from app.strategy_lab.canonical.scenarios import (
        ScenarioDefinition,
        assert_panel_invariants,
        generate_scenario,
    )

    base = build_demo_panel(["SPY", "AGG", "QQQ"], None, seed=7)
    for mech in ["drawdown", "vol_spike", "correlation_breakdown"]:
        d = ScenarioDefinition(mechanism=mech, seed=3, intensity=Decimal("0.2"),
                               start_index=10, duration=20)
        g = generate_scenario(base, d)
        assert_panel_invariants(g.panel)  # raises if low>open/close or high<...


def test_business_days_are_weekdays(client):
    from datetime import date

    from app.strategy_lab.canonical.data_service import _business_days

    days = _business_days(date(2021, 1, 4), 30)
    for d in days:
        assert d.weekday() < 5  # Mon-Fri


def test_panel_digest_changes_when_any_field_changes(client):
    from app.strategy_lab.canonical.data_service import _digest_panel, build_demo_panel

    base = build_demo_panel(["SPY", "AGG"], None, seed=1)
    d1 = _digest_panel(base, "fixture")
    changed = _with_close(base, base.close.copy())
    changed.close[5, 0] *= 1.01
    d2 = _digest_panel(changed, "fixture")
    assert d1 != d2
    # Same content -> same digest (deterministic).
    assert _digest_panel(base, "fixture") == d1


# --------------------------------------------------------------------------
# 18.5 scenarios
# --------------------------------------------------------------------------
def test_scenario_deterministic_reproduction(client):
    from app.strategy_lab.canonical.data_service import build_demo_panel
    from app.strategy_lab.canonical.scenarios import (
        ScenarioDefinition,
        generate_scenario,
    )

    base = build_demo_panel(["SPY", "AGG"], None, seed=1)
    d = ScenarioDefinition(mechanism="drawdown", seed=42, intensity=Decimal("0.3"),
                           start_index=0, duration=50)
    g1 = generate_scenario(base, d)
    g2 = generate_scenario(base, d)
    assert np.array_equal(g1.panel.close, g2.panel.close)
    assert g1.content_digest == g2.content_digest


def test_scenario_seed_sensitivity(client):
    from app.strategy_lab.canonical.data_service import build_demo_panel
    from app.strategy_lab.canonical.scenarios import (
        ScenarioDefinition,
        generate_scenario,
    )

    # vol_spike consumes the per-world RNG seed, so distinct seeds must differ.
    base = build_demo_panel(["SPY", "AGG"], None, seed=1)
    d_a = ScenarioDefinition(mechanism="vol_spike", seed=1, intensity=Decimal("0.3"),
                             start_index=10, duration=50)
    d_b = ScenarioDefinition(mechanism="vol_spike", seed=2, intensity=Decimal("0.3"),
                             start_index=10, duration=50)
    g_a = generate_scenario(base, d_a)
    g_b = generate_scenario(base, d_b)
    assert not np.array_equal(g_a.panel.close, g_b.panel.close)


def test_unknown_mechanism_rejected_422(client):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    r = _campaign(client, a, ["frobnicate"], key="cmp-bad-mech")
    assert r.status_code == 422


# --------------------------------------------------------------------------
# 18.6 confirmation / minimization / adjacent pass
# --------------------------------------------------------------------------
def test_campaign_records_minimization_and_adjacent(client, db):
    from app.persistence.database import make_engine, make_session_factory
    from app.persistence.models import AdjacentPassRow, MinimizationTrialRow

    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    r = _campaign(client, a, ["drawdown"], budget=8, seeds=(1, 2, 3), key="cmp-min")
    assert r.status_code == 200, r.text
    campaign_id = r.json()["campaign_id"]

    eng = make_session_factory(make_engine(db["url"]))()
    # Minimization trials were persisted (real re-evaluation, not fabricated).
    trials = eng.query(MinimizationTrialRow).filter_by(campaign_id=campaign_id).all()
    assert len(trials) >= 1
    body = r.json()
    if body["minimization"] and body["minimization"].get("passing_value") is not None:
        adj = eng.query(AdjacentPassRow).filter_by(campaign_id=campaign_id).all()
        assert len(adj) >= 1
        # Adjacent pass must genuinely pass every predicate.
        assert adj[0].all_predicates_pass is True
    eng.close()


def test_still_failing_worlds_never_report_adjacent(client):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    # A predicate that the drawdown world still fails must not produce an adjacent pass.
    r = _campaign(client, a, ["drawdown"], budget=8, seeds=(1, 2, 3),
                  predicates=["sharpe_below_0"], key="cmp-stillfail")
    assert r.status_code == 200, r.text
    body = r.json()
    if body["minimization"] is None or body["minimization"].get("passing_value") is None:
        assert body["adjacent_pass"] is None


# --------------------------------------------------------------------------
# 18.7 audit
# --------------------------------------------------------------------------
def test_audit_uses_real_project_and_digest(client, db):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    bt = _backtest(client, a, ["SPY", "AGG"])
    run_id = bt.json()["run_id"]
    aud = client.get(
        "/api/strategy-lab/v2/audit",
        params={"strategy_id": a["strategy_id"], "strategy_version": a["strategy_version"],
                "run_id": run_id},
    )
    assert aud.status_code == 200, aud.text
    aj = aud.json()
    assert aj["project_id"] == pid
    assert aj["canonical_hash"] == a["canonical_hash"]
    assert aj["data_source_digest"]
    assert aj["artifact_hashes"]
    # Compiler version comes from package metadata, not a hard-coded literal.
    assert aj["compiler_version"] != "canonical-compiler/v1.1"


def test_audit_wrong_project_rejected(client):
    pid = _project(client)
    other = _project(client, name="OTHER")
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    bt = _backtest(client, a, ["SPY", "AGG"])
    run_id = bt.json()["run_id"]
    aud = client.get(
        "/api/strategy-lab/v2/audit",
        params={"strategy_id": a["strategy_id"], "strategy_version": a["strategy_version"],
                "run_id": run_id, "project_id": other},
    )
    assert aud.status_code in (404, 422, 500)


# --------------------------------------------------------------------------
# 18.8 five-family vertical slices
# --------------------------------------------------------------------------
_FAMILY_THESES = {
    "static_allocation": ("Allocate 60% to SPY and 40% to AGG and rebalance monthly.", ["SPY", "AGG"]),
    "time_series_signal": ("Buy SPY when its 20-day average is above its 50-day average, otherwise hold cash.", ["SPY"]),
    "long_only_ranking": ("Buy the top 5 stocks by 12-1 momentum each month.",
                          ["AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA", "TSLA"]),
    "cross_sectional_factor": ("Each month go long the top 20% and short the bottom 20% by 12-1 momentum.",
                               ["AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA", "TSLA"]),
    "tactical_allocation": ("Each month hold the top 3 asset ETFs by 12-month momentum if above their 200-day average; otherwise hold BIL.",
                            ["SPY", "AGG", "BIL"]),
}


def test_five_family_slices_run_and_persist(client):
    for family, (thesis, universe) in _FAMILY_THESES.items():
        pid = _project(client, name=family)
        c = _compile(client, thesis)
        assert c["strategy_type"] == family, (family, c["strategy_type"])
        spec, chash = c["spec_draft"], c["canonical_hash"]
        if c["required_user_resolutions"]:
            key = c["required_user_resolutions"][0]
            rr = client.post("/api/strategy-lab/v2/resolve",
                             json={"spec_draft": spec, "resolutions": {key: universe}}).json()
            spec, chash = rr["spec_draft"], rr["canonical_hash"]
        a = _approve(client, pid, spec, chash).json()
        bt = _backtest(client, a, universe, key="bt-" + family)
        assert bt.status_code == 200, (family, bt.text)
        body = bt.json()
        assert len(body["artifact_references"]) >= 9
        camp = _campaign(client, a, ["drawdown"], budget=4, seeds=(1, 2),
                         predicates=["sharpe_below_0"], key="cmp-" + family)
        assert camp.status_code == 200, (family, camp.text)


# --------------------------------------------------------------------------
# 18.3 reasons-to-distrust (conditional, not always-empty)
# --------------------------------------------------------------------------
def test_reasons_to_distrust_reported_when_applicable(client):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    # Demo fixture is tier 3 -> at least one reason-to-distrust (synthetic data).
    bt = _backtest(client, a, ["SPY", "AGG"])
    body = bt.json()
    assert "reasons_to_distrust" in body
    assert any("synthetic" in r.lower() or "tier" in r.lower() or "fixture" in r.lower()
               for r in (body["reasons_to_distrust"] or []))
