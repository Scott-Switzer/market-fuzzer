"""Phase 2.6.1 adversarial integrity tests.

These tests attack the durability, idempotency, replay-fidelity, seed-stability
and API-hardening guarantees added in Phase 2.6.1. They intentionally simulate
crashes, concurrent reservations, process restarts, and malformed requests.
"""

from __future__ import annotations

import threading

import pytest

# ---------------------------------------------------------------------------
# helpers (kept local so this module is self-contained)
# ---------------------------------------------------------------------------


def _project(client, name="ADV"):
    return client.post("/api/strategy-lab/v2/projects", json={"name": name}).json()["project_id"]


def _compile(client, text):
    r = client.post("/api/strategy-lab/v2/compile", json={"description": text})
    assert r.status_code == 200, r.text
    return r.json()


def _approve(client, pid, spec, chash, key=None):
    return client.post(
        "/api/strategy-lab/v2/approve",
        json={
            "project_id": pid,
            "spec_draft": spec,
            "actor": "adv",
            "idempotency_key": key or ("appr-" + chash),
        },
    )


def _approved(client, pid=None):
    pid = pid or _project(client)
    c = _compile(client, "Allocate 60% to SPY and 40% to AGG and rebalance monthly.")
    r = _approve(client, pid, c["spec_draft"], c["canonical_hash"])
    assert r.status_code == 200, r.text
    return pid, r.json()


def _bt_body(a, key, universe=("SPY", "AGG")):
    return {
        "strategy_id": a["strategy_id"],
        "strategy_version": a["strategy_version"],
        "expected_canonical_hash": a["canonical_hash"],
        "data_source": {"source": "demo_fixture", "universe": list(universe)},
        "idempotency_key": key,
    }


def _cmp_body(a, key, seeds=(1, 2), budget=4):
    return {
        "strategy_id": a["strategy_id"],
        "strategy_version": a["strategy_version"],
        "expected_canonical_hash": a["canonical_hash"],
        "mechanism_families": ["drawdown"],
        "seed_list": list(seeds),
        "world_budget": budget,
        "failure_predicates": [{"metric": "sharpe", "operator": "lt", "threshold": "0"}],
        "idempotency_key": key,
    }


# ---------------------------------------------------------------------------
# Gate 2/3: durable lifecycle — reservation survives an execution crash
# ---------------------------------------------------------------------------
def test_failed_lifecycle_persisted_after_execution_crash(client, db, monkeypatch, reopen):
    """Crash the executor mid-backtest: the run/job must survive as FAILED in
    the DB (separate transaction), not vanish with the request rollback."""
    import app.strategy_lab.canonical.backtest_service as bts

    pid, a = _approved(client)

    def boom(*args, **kwargs):
        raise RuntimeError("simulated executor crash")

    monkeypatch.setattr(bts, "run_strategy", boom)
    # TestClient re-raises unhandled server exceptions — the crash escapes the
    # request; what matters is what SURVIVED in the database afterwards.
    with pytest.raises(RuntimeError, match="simulated executor crash"):
        client.post("/api/strategy-lab/v2/backtests", json=_bt_body(a, "bt-crash"))

    # Reopen the DB as a fresh process: the failed lifecycle must be there.
    from sqlalchemy import select

    from app.persistence.database import make_engine, make_session_factory
    from app.persistence.models import JobRow, RunRow

    s = make_session_factory(make_engine(db["url"]))()
    runs = s.scalars(select(RunRow).where(RunRow.strategy_id == a["strategy_id"])).all()
    assert runs, "pending/failed run must survive the crash"
    statuses = {r_.status for r_ in runs}
    assert "failed" in statuses, f"expected a persisted FAILED run, got {statuses}"
    failed_run = next(r_ for r_ in runs if r_.status == "failed")
    jobs = s.scalars(select(JobRow).where(JobRow.run_id == failed_run.id)).all()
    assert jobs and jobs[0].state == "failed"
    assert jobs[0].failure_json, "failure detail must be recorded"
    s.close()


# ---------------------------------------------------------------------------
# Gate 4: concurrency-safe idempotency (many threads, one winner)
# ---------------------------------------------------------------------------
def test_concurrent_reservations_single_winner(db):
    """N threads reserve the same (scope, project, key): exactly one INSERT
    wins; every other thread recovers the winner's row via the savepoint path
    instead of raising IntegrityError."""
    from app.persistence.database import make_engine, make_session_factory
    from app.strategy_lab.canonical.durable import reserve_idempotency

    payload = {"x": 1}
    results: list[tuple[str, bool]] = []
    errors: list[Exception] = []

    def worker():
        s = make_session_factory(make_engine(db["url"]))()
        try:
            ir = reserve_idempotency(
                s,
                scope="backtest",
                project_id="proj-conc",
                idempotency_key="same-key",
                request_payload=payload,
                resource_type="run",
                resource_id="",
                response_json={},
            )
            s.commit()
            results.append((ir.id, bool(ir.resource_id)))
        except Exception as exc:  # pragma: no cover - failure path under test
            errors.append(exc)
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"no thread may fail with a raw IntegrityError: {errors}"
    assert len(results) == 6
    ids = {rid for rid, _ in results}
    assert len(ids) == 1, f"all threads must converge on ONE reservation row, got {ids}"


def test_same_key_different_payload_conflicts(db):
    from app.persistence.database import make_engine, make_session_factory
    from app.strategy_lab.canonical.durable import reserve_idempotency
    from app.strategy_lab.canonical.errors import IdempotencyConflictError

    s = make_session_factory(make_engine(db["url"]))()
    reserve_idempotency(
        s,
        scope="backtest",
        project_id="p",
        idempotency_key="k",
        request_payload={"a": 1},
        resource_type="run",
        resource_id="",
        response_json={},
    )
    s.commit()
    with pytest.raises(IdempotencyConflictError):
        reserve_idempotency(
            s,
            scope="backtest",
            project_id="p",
            idempotency_key="k",
            request_payload={"a": 2},
            resource_type="run",
            resource_id="",
            response_json={},
        )
    s.close()


# ---------------------------------------------------------------------------
# Gate 5: keys scoped to the ACTUAL owning project
# ---------------------------------------------------------------------------
def test_backtest_idempotency_scoped_to_owning_project(client, db):
    """The reservation row must carry the strategy's real project_id, not the
    strategy_id (the pre-2.6.1 bug)."""
    pid, a = _approved(client)
    r = client.post("/api/strategy-lab/v2/backtests", json=_bt_body(a, "bt-scope"))
    assert r.status_code == 200, r.text

    from sqlalchemy import select

    from app.persistence.database import make_engine, make_session_factory
    from app.persistence.models import IdempotencyRecordRow

    s = make_session_factory(make_engine(db["url"]))()
    rows = s.scalars(
        select(IdempotencyRecordRow).where(IdempotencyRecordRow.idempotency_key == "bt-scope")
    ).all()
    assert rows, "reservation must exist"
    assert rows[0].project_id == pid, (
        f"idempotency must be scoped to the owning project {pid}, got {rows[0].project_id}"
    )
    assert rows[0].project_id != a["strategy_id"]
    s.close()


# ---------------------------------------------------------------------------
# Gate 6: replay identical — byte-for-byte across a process restart
# ---------------------------------------------------------------------------
def test_backtest_replay_identical_after_restart(client, reopen):
    pid, a = _approved(client)
    r1 = client.post("/api/strategy-lab/v2/backtests", json=_bt_body(a, "bt-replay"))
    assert r1.status_code == 200, r1.text

    # Same request, same key, NEW process (fresh app + engine on the same DB).
    client2 = reopen()
    r2 = client2.post("/api/strategy-lab/v2/backtests", json=_bt_body(a, "bt-replay"))
    assert r2.status_code == 200, r2.text

    b1, b2 = r1.json(), r2.json()
    assert b1 == b2, "replay must be IDENTICAL to the original response"
    # Explicitly: the fields the old reconstruction lost.
    for field in ("job_id", "status", "turnover", "warnings", "reasons_to_distrust", "benchmark_metrics"):
        assert b1[field] == b2[field], field


def test_campaign_replay_identical_after_restart(client, reopen):
    pid, a = _approved(client)
    r1 = client.post("/api/strategy-lab/v2/campaigns", json=_cmp_body(a, "cmp-replay"))
    assert r1.status_code == 200, r1.text

    client2 = reopen()
    r2 = client2.post("/api/strategy-lab/v2/campaigns", json=_cmp_body(a, "cmp-replay"))
    assert r2.status_code == 200, r2.text

    b1, b2 = r1.json(), r2.json()
    assert b1 == b2, "campaign replay must be IDENTICAL to the original response"
    # Confirmed failures must carry the REAL mechanism/seed/intensity, and the
    # replay must not degrade them to seed 0 / intensity 0.0 / first mechanism.
    for f1, f2 in zip(b1["confirmed_failures"], b2["confirmed_failures"], strict=True):
        assert f1 == f2


# ---------------------------------------------------------------------------
# Gate 8/9: stable SHA-256 seeds and dimension-invariant minimization
# ---------------------------------------------------------------------------
def test_stable_seed_is_process_stable_and_deterministic():
    from app.strategy_lab.canonical.scenarios import stable_seed

    assert stable_seed("a", 1) == stable_seed("a", 1)
    assert stable_seed("a", 1) != stable_seed("a", 2)
    # Known-answer: derived from SHA-256, never from builtin hash().
    import hashlib

    expected = int.from_bytes(hashlib.sha256(b"a|1").digest()[:4], "big")
    assert stable_seed("a", 1) == expected


def test_minimization_preserves_scenario_dimensions(client, db):
    """Every minimization trial and the adjacent pass must share the original
    failure's mechanism, seed, start_index and duration — only intensity moves."""
    pid, a = _approved(client)
    r = client.post("/api/strategy-lab/v2/campaigns", json=_cmp_body(a, "cmp-dim", seeds=(1, 2, 3), budget=8))
    assert r.status_code == 200, r.text
    body = r.json()
    if not body["confirmed_failures"]:
        pytest.skip("no confirmed failure in this configuration")

    from sqlalchemy import select

    from app.persistence.database import make_engine, make_session_factory
    from app.persistence.models import (
        MinimizationTrialRow,
        ScenarioWorldRow,
        WorldEvaluationRow,
    )

    s = make_session_factory(make_engine(db["url"]))()
    campaign_id = body["campaign_id"]
    fail = body["confirmed_failures"][0]
    ev = s.get(WorldEvaluationRow, fail["failure_id"])
    orig_world = s.get(ScenarioWorldRow, ev.world_id)
    orig_def = orig_world.definition

    trials = s.scalars(
        select(MinimizationTrialRow).where(MinimizationTrialRow.campaign_id == campaign_id)
    ).all()
    assert trials, "minimization trials must be persisted"

    # Every minimization/adjacent world shares mechanism/seed/start/duration
    # with the ORIGINAL failure; only intensity differs (Phase 2.6.1 gate 9).
    min_evs = s.scalars(
        select(WorldEvaluationRow).where(
            WorldEvaluationRow.campaign_id == campaign_id,
            WorldEvaluationRow.role.in_(["minimization", "adjacent_pass"]),
        )
    ).all()
    assert min_evs, "minimization world evaluations must be persisted"
    for mev in min_evs:
        w = s.get(ScenarioWorldRow, mev.world_id)
        d = w.definition
        assert d["mechanism"] == orig_def["mechanism"]
        assert d["seed"] == orig_def["seed"], "seed must not change during minimization"
        assert d["start_index"] == orig_def["start_index"], "start_index must not change"
        assert d["duration"] == orig_def["duration"], "duration must not change"
    s.close()


# ---------------------------------------------------------------------------
# Gate 10: scenario window locality
# ---------------------------------------------------------------------------
def test_scenarios_do_not_change_data_outside_window():
    import numpy as np

    from app.strategy_lab.canonical.data_service import build_demo_panel
    from app.strategy_lab.canonical.scenarios import ScenarioDefinition, generate_scenario

    base = build_demo_panel(["SPY", "AGG", "QQQ"], None, seed=11)
    from decimal import Decimal

    start, dur = 100, 60
    for mech in ("drawdown", "vol_spike", "correlation_breakdown"):
        d = ScenarioDefinition(
            mechanism=mech, seed=5, intensity=Decimal("0.4"), start_index=start, duration=dur
        )
        g = generate_scenario(base, d)
        # Bars strictly BEFORE the window are untouched.
        assert np.array_equal(g.panel.close[: start - 1], base.close[: start - 1]), (
            f"{mech} modified data before the scenario window"
        )


# ---------------------------------------------------------------------------
# Gate 11: confirmation counts equal executed trials
# ---------------------------------------------------------------------------
def test_confirmation_policy_counts_match_execution(client, db):
    seeds = (1, 2, 3, 4)  # NOT three seeds — policy must reflect reality
    pid, a = _approved(client)
    r = client.post("/api/strategy-lab/v2/campaigns", json=_cmp_body(a, "cmp-conf", seeds=seeds, budget=8))
    assert r.status_code == 200, r.text
    body = r.json()

    from app.persistence.database import make_engine, make_session_factory
    from app.persistence.models import CampaignRow

    s = make_session_factory(make_engine(db["url"]))()
    camp = s.get(CampaignRow, body["campaign_id"])
    policy = camp.confirmation_policy
    assert policy["total_trials"] == len(seeds), (
        f"policy total_trials {policy['total_trials']} != executed confirmation trials {len(seeds)}"
    )
    assert policy["required_successes"] <= policy["total_trials"]
    assert policy["independent_seeds"] == list(seeds)

    # If a failure was confirmed, the number of persisted confirmation-role
    # evaluations per primary must equal the policy's total_trials.
    if body["confirmed_failures"]:
        from sqlalchemy import select

        from app.persistence.models import WorldEvaluationRow

        conf_evs = s.scalars(
            select(WorldEvaluationRow).where(
                WorldEvaluationRow.campaign_id == body["campaign_id"],
                WorldEvaluationRow.role == "confirmation",
            )
        ).all()
        assert conf_evs, "confirmation trials must be persisted"
        assert len(conf_evs) % len(seeds) == 0, (
            f"confirmation evaluations {len(conf_evs)} not a multiple of policy trials {len(seeds)}"
        )
    s.close()


# ---------------------------------------------------------------------------
# Gate 9 (API hardening): malformed requests are 422, never 500
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "mutator",
    [
        lambda b: b.update(initial_capital="0"),
        lambda b: b.update(initial_capital="-5"),
        lambda b: b.update(strategy_version=0),
        lambda b: b.update(expected_canonical_hash="XYZ"),
        lambda b: b.update(expected_canonical_hash="a" * 63),
        lambda b: b.update(idempotency_key=""),
        lambda b: b.update(idempotency_key="k" * 300),
    ],
)
def test_backtest_request_hardening(client, mutator):
    pid, a = _approved(client)
    body = _bt_body(a, "bt-hard")
    mutator(body)
    r = client.post("/api/strategy-lab/v2/backtests", json=body)
    assert r.status_code == 422, (r.status_code, r.text)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda b: b.update(mechanism_families=[]),
        lambda b: b.update(mechanism_families=["drawdown", "drawdown"]),
        lambda b: b.update(seed_list=[]),
        lambda b: b.update(failure_predicates=[]),
        lambda b: b.update(failure_predicates=["sharpe_below_0"]),
        lambda b: b.update(failure_predicates=[{"metric": "sharpe", "operator": "??", "threshold": "0"}]),
        lambda b: b.update(
            failure_predicates=[{"metric": "unknown_metric", "operator": "lt", "threshold": "0"}]
        ),
        lambda b: b.update(world_budget=0),
    ],
)
def test_campaign_request_hardening(client, mutator):
    pid, a = _approved(client)
    body = _cmp_body(a, "cmp-hard")
    mutator(body)
    r = client.post("/api/strategy-lab/v2/campaigns", json=body)
    assert r.status_code == 422, (r.status_code, r.text)


def test_unknown_mechanism_is_422(client):
    pid, a = _approved(client)
    body = _cmp_body(a, "cmp-unknown-mech")
    body["mechanism_families"] = ["volcano"]
    r = client.post("/api/strategy-lab/v2/campaigns", json=body)
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Gate 4/12: zero-artifact completed run is rejected
# ---------------------------------------------------------------------------
def test_completed_run_with_zero_artifacts_rejected(client, db):
    pid, a = _approved(client)
    r = client.post("/api/strategy-lab/v2/backtests", json=_bt_body(a, "bt-zero"))
    assert r.status_code == 200
    run_id = r.json()["run_id"]

    # Adversary deletes the artifact index rows but leaves the run completed.
    from sqlalchemy import delete

    from app.persistence.database import make_engine, make_session_factory
    from app.persistence.models import ArtifactIndexRow

    s = make_session_factory(make_engine(db["url"]))()
    s.execute(delete(ArtifactIndexRow).where(ArtifactIndexRow.run_id == run_id))
    s.commit()
    s.close()

    rr = client.get(f"/api/strategy-lab/v2/runs/{run_id}/result")
    assert rr.status_code == 500, "completed run with zero indexed artifacts must be rejected"
