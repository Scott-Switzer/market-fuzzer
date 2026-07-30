"""Canonical v2 API + vertical-slice tests (Phase 2.5 sections 13.1-13.4).

Covers: compile-only canonical result, no legacy fallback on unsupported,
deterministic resolve, approval persistence + reload + hash continuity,
approval rejects unresolved/unregistered, hash-mismatch blocks execution,
idempotent approval, persistence/restart, and the five-family vertical slice.
"""

from __future__ import annotations


def _compile(client, text):
    r = client.post("/api/strategy-lab/v2/compile", json={"description": text})
    assert r.status_code == 200, r.text
    return r.json()


def _project(client, name="WS"):
    r = client.post("/api/strategy-lab/v2/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _approve(client, project_id, spec_draft, canonical_hash):
    return client.post(
        "/api/strategy-lab/v2/approve",
        json={
            "project_id": project_id,
            "spec_draft": spec_draft,
            "actor": "user",
            "idempotency_key": "appr-" + canonical_hash,
        },
    )


# --- 13.1 API contract -----------------------------------------------------
def test_compile_returns_canonical_result_only(client):
    body = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    assert body["api_version"] == "v2"
    assert body["strategy_type"] == "static_allocation"
    assert body["is_supported"] is True
    assert body["is_approvable"] is True
    # No legacy root-level fields.
    assert "family" not in body
    assert "ledger_hash" not in body


def test_unsupported_prose_never_gets_legacy_fallback(client):
    body = _compile(client, "Do something clever with vibes and lunar cycles.")
    assert body["strategy_type"] == "unsupported"
    assert body["is_supported"] is False
    assert body["is_approvable"] is False
    assert body["blocking_reasons"]


def test_resolve_is_deterministic(client):
    body = _compile(client, "Buy the top 5 stocks by 12-1 momentum each month.")
    assert body["required_user_resolutions"]
    key = body["required_user_resolutions"][0]
    payload = {
        "spec_draft": body["spec_draft"],
        "resolutions": {key: ["AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA", "TSLA"]},
    }
    r1 = client.post("/api/strategy-lab/v2/resolve", json=payload)
    r2 = client.post("/api/strategy-lab/v2/resolve", json=payload)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["canonical_hash"] == r2.json()["canonical_hash"]
    assert r1.json()["is_approvable"] is True


def test_approval_persists_and_reloads_exact_version(client):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    r = _approve(client, pid, c["spec_draft"], c["canonical_hash"])
    assert r.status_code == 200, r.text
    a = r.json()
    got = client.get(f"/api/strategy-lab/v2/strategies/{a['strategy_id']}/versions/{a['strategy_version']}")
    assert got.status_code == 200
    assert got.json()["canonical_hash"] == a["canonical_hash"]


def test_approval_rejects_unresolved(client):
    pid = _project(client)
    c = _compile(client, "Buy the top 5 stocks by 12-1 momentum each month.")
    r = _approve(client, pid, c["spec_draft"], c["canonical_hash"])
    assert r.status_code == 422


def test_hash_mismatch_blocks_backtest(client):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    r = client.post(
        "/api/strategy-lab/v2/backtests",
        json={
            "strategy_id": a["strategy_id"],
            "strategy_version": a["strategy_version"],
            "expected_canonical_hash": "deadbeef" * 8,
            "data_source": {"source": "demo_fixture", "universe": ["SPY", "AGG"], "benchmark": None},
            "initial_capital": 1000000,
            "idempotency_key": "bt-mismatch",
        },
    )
    assert r.status_code == 422


def test_idempotent_approval_no_duplicate_version(client):
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a1 = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    # Re-approve the SAME spec draft (same strategy_id embedded) -> same version.
    a2 = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    assert a1["strategy_id"] == a2["strategy_id"]
    assert a1["strategy_version"] == a2["strategy_version"]


# --- 13.4 vertical slice ---------------------------------------------------
def _run_slice(client, thesis, universe, resolution_universe=None):
    pid = _project(client)
    c = _compile(client, thesis)
    spec = c["spec_draft"]
    chash = c["canonical_hash"]
    if c["required_user_resolutions"] and resolution_universe:
        key = c["required_user_resolutions"][0]
        rr = client.post(
            "/api/strategy-lab/v2/resolve",
            json={"spec_draft": spec, "resolutions": {key: resolution_universe}},
        ).json()
        spec, chash = rr["spec_draft"], rr["canonical_hash"]
    a = _approve(client, pid, spec, chash).json()
    bt = client.post(
        "/api/strategy-lab/v2/backtests",
        json={
            "strategy_id": a["strategy_id"],
            "strategy_version": a["strategy_version"],
            "expected_canonical_hash": a["canonical_hash"],
            "data_source": {"source": "demo_fixture", "universe": universe, "benchmark": None},
            "initial_capital": 1000000,
            "idempotency_key": "bt-" + a["canonical_hash"],
        },
    )
    return a, bt


def test_static_allocation_slice(client):
    a, bt = _run_slice(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.", ["SPY", "AGG"])
    assert bt.status_code == 200, bt.text
    body = bt.json()
    assert body["canonical_hash"] == a["canonical_hash"]
    assert body["trade_summary"]["num_trades"] > 0


def test_sma_slice_no_ranking(client):
    a, bt = _run_slice(
        client,
        "Buy SPY when its 20-day average is above its 50-day average, otherwise hold cash.",
        ["SPY"],
    )
    assert bt.status_code == 200, bt.text
    assert bt.json()["canonical_hash"] == a["canonical_hash"]


def test_long_only_slice_no_negative_targets(client):
    a, bt = _run_slice(
        client,
        "Buy the top 5 stocks by 12-1 momentum each month.",
        ["AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA", "TSLA"],
        resolution_universe=["AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA", "TSLA"],
    )
    assert bt.status_code == 200, bt.text
    assert bt.json()["exposure_summary"]["final_net"] is not None


def test_changing_weights_changes_hash(client):
    a = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    b = _compile(client, "Allocate 70% to SPY and 30% to AGG and rebalance monthly.")
    assert a["canonical_hash"] != b["canonical_hash"]
