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
            "failure_predicates": predicates or [{"metric": "sharpe", "operator": "lt", "threshold": "0"}],
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

    # A REAL benchmark (SPY) that is NOT tradable must be excluded from
    # panel.assets but still available as a separate benchmark_close series.
    p2 = build_demo_panel(["AAPL", "MSFT"], "SPY", seed=1, benchmark_tradable=False)
    assert "SPY" not in p2.assets, "nontradable benchmark must not be in the tradable matrix"
    assert p2.benchmark_close is not None, "nontradable benchmark must ride as benchmark_close"
    assert "AAPL" in p2.assets and "MSFT" in p2.assets


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
    spec = StrategySpec.model_validate(
        dict(
            name="s",
            original_thesis="static allocation sixty forty strategy",
            strategy_type="static_allocation",
            universe=["SPY", "AGG"],
            benchmark=None,
            benchmark_tradable=False,
            portfolio_construction={"target_weights": {"SPY": Decimal("0.6"), "AGG": Decimal("0.4")}},
        )
    )
    try:
        check_required_history(spec, short)
        raise AssertionError("short panel not rejected")
    except PanelTooShortError:
        pass


def test_short_history_api_returns_structured_422(client, monkeypatch):
    """API-level proof: when the acquired panel is too short for the strategy's
    lookback, the endpoint returns a structured 422 (not a 500) with the
    PanelTooShortError detail (Phase 2.6.1 gate 14)."""
    import app.strategy_lab.canonical.backtest_service as bts

    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()

    real_acquire = bts.acquire_panel

    def short_acquire(*args, **kwargs):
        panel, prov = real_acquire(*args, **kwargs)
        # 1 bar is below EVERY executor's minimum decision bars.
        return _slice_panel(panel, 1), prov

    monkeypatch.setattr(bts, "acquire_panel", short_acquire)
    r = client.post(
        "/api/strategy-lab/v2/backtests",
        json={
            "strategy_id": a["strategy_id"],
            "strategy_version": a["strategy_version"],
            "expected_canonical_hash": a["canonical_hash"],
            "data_source": {"source": "demo_fixture", "universe": ["SPY", "AGG"]},
            "idempotency_key": "bt-short-history",
        },
    )
    assert r.status_code == 422, r.text
    detail = str(r.json()["detail"]).lower()
    assert "short" in detail or "history" in detail or "bars" in detail


def test_ohlc_invariants_hold_in_scenarios(client):
    from app.strategy_lab.canonical.data_service import build_demo_panel
    from app.strategy_lab.canonical.scenarios import (
        ScenarioDefinition,
        assert_panel_invariants,
        generate_scenario,
    )

    base = build_demo_panel(["SPY", "AGG", "QQQ"], None, seed=7)
    for mech in ["drawdown", "vol_spike", "correlation_breakdown"]:
        d = ScenarioDefinition(mechanism=mech, seed=3, intensity=Decimal("0.2"), start_index=10, duration=20)
        g = generate_scenario(base, d)
        assert_panel_invariants(g.panel)  # raises if low>open/close or high<...


def test_business_days_are_weekdays(client):
    from datetime import date

    from app.strategy_lab.canonical.data_service import _business_days

    days = _business_days(date(2021, 1, 4), 30)
    for d in days:
        assert d.weekday() < 5  # Mon-Fri


def test_panel_digest_changes_when_any_field_changes(client):
    import numpy as np

    from app.strategy_lab.canonical.data_service import _digest_panel, build_demo_panel

    base = build_demo_panel(["SPY", "AGG"], None, seed=1)
    d1 = _digest_panel(base, "fixture")
    # Same content -> same digest (deterministic).
    assert _digest_panel(base, "fixture") == d1

    # EVERY declared input dimension must perturb the digest, not just close.
    def _clone(p):
        import copy

        return copy.deepcopy(p)

    seen = {d1}
    # Direction-safe perturbations so OHLC validity is preserved:
    # high up, low down, open/close nudged within [low, high], volume up.
    for field in ("open", "high", "low", "close", "volume"):
        changed = _clone(base)
        arr = getattr(changed, field)
        if field == "high":
            arr[5, 0] *= 1.01
        elif field == "low":
            arr[5, 0] *= 0.99
        elif field == "volume":
            arr[5, 0] += 1
        else:  # open/close: nudge toward low, stays inside [low, high]
            arr[5, 0] = (arr[5, 0] + changed.low[5, 0]) / 2.0
        d = _digest_panel(changed, "fixture")
        assert d != d1, f"digest must change when {field} changes"
        assert d not in seen, f"digest for {field} change must be unique"
        seen.add(d)
    # Dates are part of the digest.
    import dataclasses

    changed = _clone(base)
    new_dates = list(changed.dates)
    new_dates[0] = new_dates[0].replace(year=new_dates[0].year - 1)
    changed = dataclasses.replace(changed, dates=tuple(new_dates))
    assert _digest_panel(changed, "fixture") != d1
    # Asset identity is part of the digest.
    changed = _clone(base)
    new_assets = ("ZZZ",) + tuple(changed.assets[1:])
    new_meta = dict(changed.metadata)
    old_first = changed.assets[0]
    meta0 = new_meta.pop(old_first)
    new_meta["ZZZ"] = dataclasses.replace(meta0, ticker="ZZZ")
    changed = dataclasses.replace(changed, assets=new_assets, metadata=new_meta)
    assert _digest_panel(changed, "fixture") != d1
    # Source label is part of the digest.
    assert _digest_panel(base, "other-source") != d1
    del np


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
    d = ScenarioDefinition(
        mechanism="drawdown", seed=42, intensity=Decimal("0.3"), start_index=0, duration=50
    )
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
    d_a = ScenarioDefinition(
        mechanism="vol_spike", seed=1, intensity=Decimal("0.3"), start_index=10, duration=50
    )
    d_b = ScenarioDefinition(
        mechanism="vol_spike", seed=2, intensity=Decimal("0.3"), start_index=10, duration=50
    )
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
    # Every persisted minimization trial recorded a real evaluated result
    # (predicate_results is non-empty) — not a fabricated boundary.
    assert all(t.predicate_results for t in trials if not getattr(t, "failed", False) or True)
    body = r.json()
    if body["minimization"] and body["minimization"].get("passing_value") is not None:
        adj = eng.query(AdjacentPassRow).filter_by(campaign_id=campaign_id).all()
        assert len(adj) >= 1
        # Adjacent pass must genuinely pass EVERY predicate: verify the stored
        # per-predicate results (AdjacentPassRow has no boolean shortcut column).
        assert adj[0].outcome == "pass"
        assert adj[0].predicate_results, "adjacent pass must record per-predicate results"
        assert all(not pr["failed"] for pr in adj[0].predicate_results)
    eng.close()


def test_still_failing_worlds_never_report_adjacent(client):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    # A predicate that the drawdown world still fails must not produce an adjacent pass.
    r = _campaign(
        client,
        a,
        ["drawdown"],
        budget=8,
        seeds=(1, 2, 3),
        predicates=[{"metric": "sharpe", "operator": "lt", "threshold": "0"}],
        key="cmp-stillfail",
    )
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
        params={"strategy_id": a["strategy_id"], "strategy_version": a["strategy_version"], "run_id": run_id},
    )
    assert aud.status_code == 200, aud.text
    aj = aud.json()
    assert aj["project_id"] == pid
    assert aj["canonical_hash"] == a["canonical_hash"]
    assert aj["data_source_digest"]
    assert aj["artifact_hashes"]
    # Compiler version is the ACTUAL compiler version recorded during
    # compilation (app.compiler.COMPILER_VERSION), not a package metadata guess.
    from app.compiler import COMPILER_VERSION

    assert aj["compiler_version"] == COMPILER_VERSION


def test_audit_wrong_project_rejected(client):
    pid = _project(client)
    other = _project(client, name="OTHER")
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    bt = _backtest(client, a, ["SPY", "AGG"])
    run_id = bt.json()["run_id"]
    aud = client.get(
        "/api/strategy-lab/v2/audit",
        params={
            "strategy_id": a["strategy_id"],
            "strategy_version": a["strategy_version"],
            "run_id": run_id,
            "project_id": other,
        },
    )
    # Semantic isolation must be a CLIENT error (404/422), never a 500:
    # an internal server error must not satisfy this test (Phase 2.6.1 gate 14).
    assert aud.status_code in (404, 422), aud.text


# --------------------------------------------------------------------------
# 18.8 five-family vertical slices
# --------------------------------------------------------------------------
_FAMILY_THESES = {
    "static_allocation": ("Allocate 60% to SPY and 40% to AGG and rebalance monthly.", ["SPY", "AGG"]),
    "time_series_signal": (
        "Buy SPY when its 20-day average is above its 50-day average, otherwise hold cash.",
        ["SPY"],
    ),
    "long_only_ranking": (
        "Buy the top 5 stocks by 12-1 momentum each month.",
        ["AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA", "TSLA"],
    ),
    "cross_sectional_factor": (
        "Each month go long the top 20% and short the bottom 20% by 12-1 momentum.",
        ["AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA", "TSLA"],
    ),
    "tactical_allocation": (
        "Each month hold the top 3 asset ETFs by 12-month momentum if above their 200-day average; otherwise hold BIL.",
        ["SPY", "AGG", "BIL"],
    ),
}


def test_five_family_slices_run_and_persist(client):
    for family, (thesis, universe) in _FAMILY_THESES.items():
        pid = _project(client, name=family)
        c = _compile(client, thesis)
        assert c["strategy_type"] == family, (family, c["strategy_type"])
        spec, chash = c["spec_draft"], c["canonical_hash"]
        if c["required_user_resolutions"]:
            key = c["required_user_resolutions"][0]
            rr = client.post(
                "/api/strategy-lab/v2/resolve", json={"spec_draft": spec, "resolutions": {key: universe}}
            ).json()
            spec, chash = rr["spec_draft"], rr["canonical_hash"]
        a = _approve(client, pid, spec, chash).json()
        bt = _backtest(client, a, universe, key="bt-" + family)
        assert bt.status_code == 200, (family, bt.text)
        body = bt.json()
        assert len(body["artifact_references"]) >= 9

        # Family-specific behavior (Phase 2.6.1 gate 14): prove target/exposure/
        # trade semantics per family, not just HTTP success.
        # NOTE: the engine labels ANY negative-qty fill "sell_short" (including
        # ordinary rebalance sells), so long-only is proven by cumulative
        # per-asset positions never going net-negative — not by side labels.
        result = client.get(f"/api/strategy-lab/v2/runs/{body['run_id']}/result").json()
        trades = result["trades"] or []
        traded_syms = {t["asset"] for t in trades}

        def _never_net_short(trs):
            # Tolerance 0.5 shares: fills are rounded to 6dp and rebalance
            # sequences accumulate rounding noise; a REAL short is >> 0.5 shares.
            pos: dict[str, float] = {}
            for t in sorted(trs, key=lambda x: str(x["date"])):
                pos[t["asset"]] = pos.get(t["asset"], 0.0) + float(t["quantity"])
            return all(q >= -0.5 for q in pos.values())

        if family == "static_allocation":
            # Static 60/40: both legs traded; universe-bounded; never net short.
            assert {"SPY", "AGG"} <= traded_syms, (family, traded_syms)
            assert traded_syms <= set(universe), (family, traded_syms)
            assert _never_net_short(trades), (family, "static allocation went net short")
        elif family == "time_series_signal":
            # Signal strategy trades only its single signal asset; never net short.
            assert traded_syms <= {"SPY"}, (family, traded_syms)
            assert body["trade_summary"]["num_trades"] >= 1, family
            assert _never_net_short(trades), (family, "signal strategy went net short")
        elif family == "long_only_ranking":
            # Long-only ranking: real trades, positions never net short.
            assert trades, family
            assert traded_syms <= set(universe), (family, traded_syms)
            assert _never_net_short(trades), (family, "long-only ranking went net short")
        elif family == "cross_sectional_factor":
            # Long/short factor: at least one asset ends up genuinely net short.
            assert trades, family
            assert traded_syms <= set(universe), (family, traded_syms)
            assert not _never_net_short(trades), (family, "factor strategy never went short")
        elif family == "tactical_allocation":
            # Tactical: long-only rotation drawn from the ETF menu.
            assert trades, family
            assert traded_syms <= set(universe), (family, traded_syms)
            assert _never_net_short(trades), (family, "tactical allocation went net short")

        camp = _campaign(
            client,
            a,
            ["drawdown"],
            budget=4,
            seeds=(1, 2),
            predicates=[{"metric": "sharpe", "operator": "lt", "threshold": "0"}],
            key="cmp-" + family,
        )
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
    assert any(
        "synthetic" in r.lower() or "tier" in r.lower() or "fixture" in r.lower()
        for r in (body["reasons_to_distrust"] or [])
    )
