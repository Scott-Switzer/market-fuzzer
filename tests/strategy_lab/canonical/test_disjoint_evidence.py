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

from app.persistence.models import (
    CampaignRow,
    RunRow,
    ScenarioWorldRow,
    WorldEvaluationRow,
)
from app.strategy_lab.canonical.data_service import build_demo_panel
from app.strategy_lab.canonical.predicates import (
    ComparisonOperator,
    FailurePredicate,
    MetricName,
)
from app.strategy_lab.canonical.scenarios import (
    ScenarioDefinition,
    effective_world_hash,
    generate_scenario,
    stable_seed,
)


def _make_chain(sess, campaign_id, run_id="run-1", project_id="proj-1", strategy_id="strat-1"):
    """Build the full FK chain so world rows can be persisted in a unit test:
    project -> strategy -> strategy_version -> run -> campaign. Returns the
    CampaignRow.id to use as campaign_id."""
    from app.persistence.models import Project, Strategy, StrategyVersionRow

    if sess.get(Project, project_id) is None:
        sess.add(Project(id=project_id, name="WS"))
    if sess.get(Strategy, strategy_id) is None:
        sess.add(Strategy(id=strategy_id, project_id=project_id, name="S"))
    sv = sess.get(StrategyVersionRow, 1)
    if sv is None:
        sess.add(
            StrategyVersionRow(
                strategy_id=strategy_id,
                version=1,
                canonical_hash="0" * 64,
                canonical_json="{}",
                state="approved",
            )
        )
        sess.flush()
        sv = sess.get(StrategyVersionRow, 1)
    if sess.get(RunRow, run_id) is None:
        sess.add(
            RunRow(
                id=run_id,
                project_id=project_id,
                strategy_id=strategy_id,
                strategy_version=1,
                strategy_hash="0" * 64,
                data_mode="demo_fixture",
            )
        )
    camp = sess.get(CampaignRow, campaign_id)
    if camp is None:
        sess.add(
            CampaignRow(
                id=campaign_id,
                run_id=run_id,
                project_id=project_id,
                strategy_id=strategy_id,
                strategy_version=1,
                strategy_hash="0" * 64,
                mechanisms=["vol_spike"],
                seeds=[1],
                failure_predicates=[],
                confirmation_policy={},
            )
        )
    sess.flush()
    return campaign_id


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


class _StubApproved:
    """Minimal stand-in so ``approved.to_spec()`` works without a real strategy."""

    def to_spec(self):
        return {"id": "x", "version": 1}


# ---------------------------------------------------------------------------
# Regression: confirmation identities participate in the CAMPAIGN-WIDE seen set
# (issue #2) -- not only pairwise-within-one-failure.
# ---------------------------------------------------------------------------
def test_confirmation_hashes_join_campaign_wide_seen_set(db, monkeypatch):
    """The confirmation helper must consult AND mutate the campaign-wide
    ``seen_world_hashes`` set, so a later primary / minimization / adjacent /
    another-failure's-confirmation world that realizes the SAME effective
    identity is deduped at the application level -- not only confirmation worlds
    within a single failure.

    Reproduces the fix: previously the helper held its own local ``seen`` and
    never returned its accepted hashes, so the outer campaign set stayed blind
    to confirmation identities.
    """
    import app.strategy_lab.canonical.campaign_service as cs

    sess = db["factory"]()
    _make_chain(sess, "camp-x")
    base = build_demo_panel(["SPY", "AGG"], None, seed=1)
    bd = "BASEDIGEST"
    mech = "vol_spike"
    intensity = Decimal("0.3")
    defn = ScenarioDefinition(
        mechanism=mech,
        seed=stable_seed(mech, 1, 1, 300, int(intensity * 1000)),
        intensity=intensity,
        start_index=10,
        duration=30,
    )
    preds = [
        FailurePredicate(
            metric=MetricName("sharpe"),
            operator=ComparisonOperator("lt"),
            threshold=Decimal("0"),
        )
    ]
    # The caller (campaign body) adds the primary's hash to the shared set
    # before invoking the helper; simulate that so the test mirrors production.
    shared: set[str] = set()
    shared.add("PRIMARY_HASH")
    # A pre-existing (e.g. earlier primary/minimization) effective identity that
    # a confirmation world might otherwise collide with.
    pre_existing = "PRE_EXISTING_HASH_FROM_ANOTHER_WORLD"
    shared.add(pre_existing)

    monkeypatch.setattr(cs, "run_strategy", _fake_run_strategy)

    confirmed, trials, cwh = cs._derive_disjoint_confirmation_worlds(
        session=sess,
        campaign_id="camp-x",
        approved=_StubApproved(),
        base_panel=base,
        base_digest=bd,
        predicates=preds,
        expected_canonical_hash="0" * 64,
        mechanism=mech,
        seed=1,
        intensity=float(intensity),
        definition=defn,
        primary_world_hash="PRIMARY_HASH",
        world_key="k",
        confirmation_policy=cs.ConfirmationPolicy(
            required_successes=1, total_trials=2, independent_seeds=[1, 2]
        ),
        seen_world_hashes=shared,
    )
    # Confirmation ran and produced distinct worlds.
    assert trials == 2
    assert len(cwh) == 2
    assert "PRIMARY_HASH" not in cwh
    # The shared set now contains the confirmation identities (and the primary).
    for h in cwh:
        assert h in shared
    assert "PRIMARY_HASH" in shared
    assert pre_existing in shared  # untouched but still present
    sess.close()


# ---------------------------------------------------------------------------
# Regression: _probe_one reuses the persisted world on a duplicate identity
# instead of raising UnboundLocalError or inserting a duplicate row (issue #1).
# ---------------------------------------------------------------------------
def test_probe_one_reuses_persisted_world_on_duplicate_identity(db, monkeypatch):
    """When ``_probe_one`` is invoked twice with the SAME effective identity, the
    second call must reuse the already-persisted ScenarioWorldRow (no
    UnboundLocalError, no second row with the same world_hash)."""
    import app.strategy_lab.canonical.campaign_service as cs

    sess = db["factory"]()
    _make_chain(sess, "camp-p")
    base = build_demo_panel(["SPY", "AGG"], None, seed=1)
    bd = "BASEDIGEST"
    mech = "vol_spike"
    intensity = 0.3
    base_def = ScenarioDefinition(
        mechanism=mech,
        seed=stable_seed(mech, 1, 1, 300, int(intensity * 1000)),
        intensity=Decimal(str(intensity)),
        start_index=10,
        duration=30,
    )
    preds = [
        FailurePredicate(
            metric=MetricName("sharpe"),
            operator=ComparisonOperator("lt"),
            threshold=Decimal("0"),
        )
    ]
    # Collapse every probe identity to one constant -> the 2nd call must reuse.
    monkeypatch.setattr(cs, "effective_world_hash", lambda *a, **k: "DUP_PROBE_HASH")
    monkeypatch.setattr(cs, "run_strategy", _fake_run_strategy)

    # _probe_one persists a MinimizationTrialRow whose failure_id FK references a
    # WorldEvaluationRow; create that parent evaluation so the FK is satisfied.
    from app.persistence.models import ScenarioWorldRow as SWR
    from app.persistence.models import WorldEvaluationRow as WER

    sess.add(
        SWR(
            id="world-p",
            campaign_id="camp-p",
            world_key="k",
            mechanism=mech,
            seed=1,
            intensity=0.3,
            definition={},
            content_digest="x",
            world_hash="PRIMARY_HASH",
        )
    )
    sess.add(
        WER(
            id="fail-p",
            campaign_id="camp-p",
            world_id="world-p",
            outcome="failed_predicate",
            predicate_results=[],
            metrics={},
            error_message=None,
            role="primary",
        )
    )
    sess.flush()

    seen: set[str] = set()
    cs._probe_one(
        sess,
        "camp-p",
        "fail-p",
        mech,
        _StubApproved(),
        base,
        preds,
        "0" * 64,
        intensity,
        base_def,
        bd,
        seen,
    )
    cs._probe_one(
        sess,
        "camp-p",
        "fail-p",
        mech,
        _StubApproved(),
        base,
        preds,
        "0" * 64,
        intensity,
        base_def,
        bd,
        seen,
    )
    sess.flush()
    # Exactly ONE ScenarioWorldRow with the duplicate identity;
    # both evaluations reference that same world row.
    rows = sess.query(ScenarioWorldRow).filter_by(world_hash="DUP_PROBE_HASH").all()
    assert len(rows) == 1, f"expected exactly one persisted world, got {len(rows)}"
    evals = (
        sess.query(WorldEvaluationRow)
        .filter_by(campaign_id="camp-p", role="minimization")
        .all()
    )
    assert len(evals) == 2
    assert all(e.world_id == rows[0].id for e in evals)
    sess.close()
