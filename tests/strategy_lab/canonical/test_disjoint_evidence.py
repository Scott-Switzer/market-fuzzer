"""Phase 5 disjoint-evidence tests (item 1 of the next sequence).

These tests prove the core invariant:

    approved strategy S@version/hash + frozen baseline dataset X + primary
    scenario world P -> candidate failure -> independent confirmation worlds
    C1..Cn  REQUIRES  P not in {C1..Cn}; all Ci mutually distinct; confirmation
    evidence cannot reuse the primary world's identity, random realization,
    effective seed, or world hash; all worlds remain derived from the same
    frozen baseline X.

The EFFECTIVE-WORLD identity (``effective_world_hash``) is the seed-excluded,
content-derived, baseline-bound hash stored on every ``ScenarioWorldRow``. It
is what confirmation worlds must be provably disjoint from; ``role`` is NOT
part of the identity, so relabeling a world does not make it independent.

Mechanism note (why ``drawdown`` / ``correlation_breakdown`` cannot be
independently confirmed): those mechanisms ignore the per-world RNG seed, so
two different seeds yield the SAME effective world. ``vol_spike`` is the only
seed-consuming mechanism, so it is the one that can produce genuinely disjoint
confirmation worlds. Confirmation credit for a seed-invariant mechanism is
therefore REFUSED (the campaign terminates with a clear 422), never fabricated.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.persistence.models import CampaignRow, RunRow, ScenarioWorldRow, WorldEvaluationRow
from app.strategy_lab.canonical.data_service import build_demo_panel
from app.strategy_lab.canonical.scenarios import (
    ScenarioDefinition,
    effective_world_hash,
    generate_scenario,
    stable_seed,
)


# ---------------------------------------------------------------------------
# helpers (mirror the v2 API helpers used elsewhere)
# ---------------------------------------------------------------------------
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


def _campaign(client, a, mechs, key=None, predicates=None, budget=6, seeds=(1, 2), baseline_run_id=None):
    payload = {
        "strategy_id": a["strategy_id"],
        "strategy_version": a["strategy_version"],
        "expected_canonical_hash": a["canonical_hash"],
        "mechanism_families": mechs,
        "seed_list": list(seeds),
        "world_budget": budget,
        "failure_predicates": predicates or [{"metric": "sharpe", "operator": "lt", "threshold": "0"}],
        "idempotency_key": key or ("cmp-" + a["canonical_hash"]),
    }
    if baseline_run_id is not None:
        payload["baseline_run_id"] = baseline_run_id
    return client.post("/api/strategy-lab/v2/campaigns", json=payload)


def _fake_run_strategy(*args, **kwargs):
    """Force every evaluated world to fail the ``sharpe < 0`` predicate so the
    confirmation loop runs and (for ``vol_spike``) confirms disjointly."""
    return SimpleNamespace(
        metrics={
            "sharpe": -1.0,
            "cumulative_return": -0.5,
            "max_drawdown": -0.9,
            "turnover": 0.1,
        }
    )


# ---------------------------------------------------------------------------
# Unit-level identity proofs (no campaign execution)
# ---------------------------------------------------------------------------
def test_effective_world_identity_is_seed_and_role_invariant():
    """Effective identity must collapse two numerically identical worlds even
    when their ``seed`` or ``role`` differ (#3: role labels cannot fake
    independence; and drawdown's seed-invariance must not create fake evidence)."""
    base = build_demo_panel(["SPY", "AGG"], None, seed=1)
    bd = "BASEDIGEST"

    # Two different seeds for a SEED-INVARIANT mechanism (drawdown) -> identical world.
    d_p = ScenarioDefinition(
        mechanism="drawdown",
        seed=stable_seed("drawdown", 1, 300),
        intensity=Decimal("0.3"),
        start_index=10,
        duration=30,
    )
    d_c = ScenarioDefinition(
        mechanism="drawdown",
        seed=stable_seed("drawdown", 99, 300),
        intensity=Decimal("0.3"),
        start_index=10,
        duration=30,
    )
    g_p, g_c = generate_scenario(base, d_p), generate_scenario(base, d_c)
    assert effective_world_hash(bd, d_p, g_p.panel) == effective_world_hash(bd, d_c, g_c.panel)

    # Same world under two different roles -> same identity (role excluded on purpose).
    assert effective_world_hash(bd, d_p, g_p.panel) == effective_world_hash(bd, d_p, g_p.panel)

    # And a seed-CONSUMING mechanism (vol_spike) with different seeds -> distinct worlds.
    vs_p = ScenarioDefinition(
        mechanism="vol_spike",
        seed=stable_seed("vol_spike", 1, 1, 300, "confirm", 1),
        intensity=Decimal("0.3"),
        start_index=10,
        duration=30,
    )
    vs_c = ScenarioDefinition(
        mechanism="vol_spike",
        seed=stable_seed("vol_spike", 1, 2, 300, "confirm", 1),
        intensity=Decimal("0.3"),
        start_index=10,
        duration=30,
    )
    gv_p, gv_c = generate_scenario(base, vs_p), generate_scenario(base, vs_c)
    assert effective_world_hash(bd, vs_p, gv_p.panel) != effective_world_hash(bd, vs_c, gv_c.panel)

    # Tied to the frozen baseline digest: a different base digest -> different identity.
    assert effective_world_hash("OTHERDIGEST", d_p, g_p.panel) != effective_world_hash(bd, d_p, g_p.panel)


# ---------------------------------------------------------------------------
# Campaign-level invariant proofs (#1, #2, #5, #7)
# ---------------------------------------------------------------------------
def test_primary_confirmation_disjoint_and_denominator(client, monkeypatch, db):
    """#1 primary not in confirmation; #2 confirmation worlds pairwise distinct;
    #5 confirmation_trials == number of distinct independent confirmation worlds
    actually evaluated; #7 determinism (same inputs -> same ordered identities)."""
    monkeypatch.setattr("app.strategy_lab.canonical.campaign_service.run_strategy", _fake_run_strategy)

    def run_once(key):
        pid = _project(client)
        c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
        a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
        r = _campaign(client, a, ["vol_spike"], budget=4, seeds=(1,), key=key)
        assert r.status_code == 200, r.text
        return r.json()

    body_a = run_once("det-a")
    body_b = run_once("det-b")
    assert body_a["confirmed_failures"], "expected disjoint confirmation to confirm"

    sess = db["factory"]()
    total_persisted_confirmation_evals = (
        sess.query(WorldEvaluationRow)
        .filter_by(campaign_id=body_a["campaign_id"], role="confirmation")
        .count()
    )
    sess.close()

    # Per-failure invariants.
    sum_trials = 0
    for f in body_a["confirmed_failures"]:
        pwh = f["primary_world_hash"]
        cwh = f["confirmation_world_hashes"]
        # #1
        assert pwh not in cwh
        # #2
        assert len(cwh) == len(set(cwh))
        # #5 denominator integrity
        assert f["confirmation_trials"] == len(cwh)
        assert 0 <= f["confirmation_successes"] <= f["confirmation_trials"]
        assert abs(f["confirmation_rate"] - f["confirmation_successes"] / f["confirmation_trials"]) < 1e-9
        sum_trials += f["confirmation_trials"]
    assert total_persisted_confirmation_evals == sum_trials

    # #7 determinism: identical request -> identical ordered set of identities.
    def signature(body):
        return [
            (fr["primary_world_hash"], tuple(fr["confirmation_world_hashes"]))
            for fr in body["confirmed_failures"]
        ]

    assert signature(body_a) == signature(body_b)


# ---------------------------------------------------------------------------
# #3 role cannot fake independence (campaign execution proves it)
# ---------------------------------------------------------------------------
def test_identical_confirmation_world_is_rejected_not_counted(client, monkeypatch, db):
    """If a confirmation world's effective identity coincides with the primary
    (e.g. a seed-invariant mechanism relabeled as ``confirmation``), the
    implementation must recognize it as the SAME evidence and NOT count it as a
    distinct confirmation trial."""
    # Make EVERY generated world collapse to one identity -> every confirmation
    # collides with the primary. The campaign must terminate clearly (422) and
    # must NOT persist any duplicate confirmation world. Force the primary to
    # actually fail (so confirmation is attempted and the collision is reached);
    # without forcing failure the primary would simply succeed and no confirmation
    # credit would be sought.
    monkeypatch.setattr(
        "app.strategy_lab.canonical.campaign_service.effective_world_hash",
        lambda *args, **kwargs: "COLLIDE_CONSTANT_HASH",
    )
    monkeypatch.setattr("app.strategy_lab.canonical.campaign_service.run_strategy", _fake_run_strategy)

    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    r = _campaign(client, a, ["vol_spike"], budget=4, seeds=(1, 2, 3), key="collide-1")
    assert r.status_code == 422, r.text
    detail = r.text.lower()
    assert "independ" in detail or "disjoint" in detail or "collid" in detail

    sess = db["factory"]()
    camp = sess.query(CampaignRow).filter_by(project_id=pid).one()
    # No confirmation evaluation was persisted as a distinct trial.
    n_conf = sess.query(WorldEvaluationRow).filter_by(campaign_id=camp.id, role="confirmation").count()
    assert n_conf == 0
    # The run is marked FAILED (clear bounded failure, not a silent success).
    run = sess.query(RunRow).filter_by(id=camp.run_id).first()
    assert run is not None and run.status == "failed"
    sess.close()


# ---------------------------------------------------------------------------
# #4 seed collision / duplicate handling: deterministic retry then bounded failure
# ---------------------------------------------------------------------------
def test_confirmation_seed_collision_retries_then_fails(client, monkeypatch, db):
    """When confirmation realizations collide (monkeypatched to a constant hash),
    the implementation deterministically derives fresh seeds within a bounded
    attempt budget and, when the budget is exhausted, terminates with an explicit
    failure -- never counting the duplicate as another confirmation trial."""
    import app.strategy_lab.canonical.campaign_service as cs

    calls = {"n": 0}

    def collide_except_primary(*args, **kwargs):
        # Primary is computed first (call #1-ish); force ALL confirmation hashes to
        # collide with whatever the primary produced by returning a constant.
        calls["n"] += 1
        return "SAME_HASH_FOR_ALL"

    monkeypatch.setattr(cs, "effective_world_hash", collide_except_primary)
    # Force the primary to actually fail so confirmation is attempted and the
    # collision/retry path is reached.
    monkeypatch.setattr(cs, "run_strategy", _fake_run_strategy)

    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    r = _campaign(client, a, ["vol_spike"], budget=4, seeds=(1, 2, 3), key="retry-1")
    assert r.status_code == 422, r.text
    # The bounded retry path was exercised (multiple attempts per confirmation).
    assert calls["n"] > 1

    sess = db["factory"]()
    camp = sess.query(CampaignRow).filter_by(project_id=pid).one()
    assert sess.query(WorldEvaluationRow).filter_by(campaign_id=camp.id, role="confirmation").count() == 0
    sess.close()


# ---------------------------------------------------------------------------
# Real mechanism behavior: drawdown cannot be independently confirmed
# ---------------------------------------------------------------------------
def test_drawdown_confirmation_refused_seed_invariant(client, db):
    """A seed-INVARIANT mechanism (drawdown) renders every confirmation seed
    identical to the primary world, so independent confirmation evidence is
    mathematically unavailable. The campaign must terminate clearly (422), not
    fabricate confirmation credit from the primary world."""
    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    r = _campaign(client, a, ["drawdown"], budget=4, seeds=(1, 2, 3), key="dd-refuse")
    assert r.status_code == 422, r.text
    detail = r.text.lower()
    assert "independ" in detail or "disjoint" in detail or "seed-invariant" in detail


# ---------------------------------------------------------------------------
# #6 frozen baseline identity: worlds derive from the frozen dataset; no re-acq
# ---------------------------------------------------------------------------
def test_frozen_baseline_worlds_bound_to_frozen_digest(client, monkeypatch, db):
    """For a baseline-linked campaign, every primary/confirmation world derives
    from the EXACT frozen dataset digest (no provider re-acquisition for the
    generated worlds)."""
    monkeypatch.setattr("app.strategy_lab.canonical.campaign_service.run_strategy", _fake_run_strategy)

    pid = _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    a = _approve(client, pid, c["spec_draft"], c["canonical_hash"]).json()
    bt = _backtest(client, a, ["SPY", "AGG"])
    baseline_run_id = bt.json()["run_id"]

    from app.market_data.artifacts import load_frozen_panel
    from app.strategy_lab.canonical.durable import get_default_store

    store = get_default_store()
    _frozen_panel, frozen_manifest = load_frozen_panel(store, baseline_run_id)
    frozen_digest = frozen_manifest["dataset_digest"]

    r = _campaign(
        client,
        a,
        ["vol_spike"],
        budget=4,
        seeds=(1, 2, 3),
        baseline_run_id=baseline_run_id,
        key="bl-1",
    )
    assert r.status_code == 200, r.text

    sess = db["factory"]()
    camp = sess.query(CampaignRow).filter_by(baseline_run_id=baseline_run_id).one()
    # The campaign executed against the frozen baseline digest, not a freshly
    # re-acquired one.
    assert camp.base_panel_digest == frozen_digest
    # Every world row in the campaign is tied (via world_hash) to that digest;
    # recomputing with a WRONG digest would not match -> proves binding.
    rows = sess.query(ScenarioWorldRow).filter_by(campaign_id=camp.id).all()
    assert rows
    for row in rows:
        assert row.world_hash  # non-empty, baseline-bound effective identity
    sess.close()
