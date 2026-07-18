from __future__ import annotations

import copy
import hashlib
import importlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.api.app import app
from app.execution_arena import CHALLENGE_ID, HIDDEN_VARIANTS, benchmark_matrix
from app.execution_store import ArenaPhaseError, ArenaStore


def policy_payload() -> dict:
    return {
        "schema_version": "1.0",
        "strategy_type": "adaptive_pov",
        "target_participation": 0.08,
        "max_participation": 0.10,
        "max_spread_bps": 12,
        "urgency_curve": "adaptive",
        "feed_latency_tolerance_ms": 10,
        "cancel_after_ms": 100,
        "completion_buffer_steps": 3,
        "pause_during_halt": True,
        "pause_above_spread_limit": True,
        "include_pending_in_budget": True,
        "rationale": "I cap participation and pause when the public book is too wide to avoid chasing noise.",
    }


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("ARENA_DB_PATH", str(tmp_path / "arena.sqlite3"))
    monkeypatch.setenv("ARENA_TEST_AUTH", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return TestClient(app)


def test_store_survives_restart_with_phase_submission_evaluation_and_audit(tmp_path) -> None:
    path = tmp_path / "restart.sqlite3"
    first = ArenaStore(path)
    first.ensure_default_challenge(CHALLENGE_ID, list(HIDDEN_VARIANTS))
    first.save_submission(
        "submission-1",
        CHALLENGE_ID,
        "student-1",
        "1.0",
        policy_payload(),
        "policy-hash",
        {"public_score": 123.0, "metrics": {"completion_pct": 100.0}},
    )
    first.transition(CHALLENGE_ID, "instructor-1", "submission_locked", "deadline")
    first.transition(CHALLENGE_ID, "instructor-1", "hidden_evaluation", "evaluate")
    matrix = benchmark_matrix(seeds=(42,))
    first.save_evaluation(CHALLENGE_ID, "instructor-1", matrix)
    first.release_evaluation(CHALLENGE_ID, "instructor-1")
    first.transition(CHALLENGE_ID, "instructor-1", "released", "publish results")

    restarted = ArenaStore(path)
    assert restarted.challenge(CHALLENGE_ID)["phase"] == "released"
    assert restarted.submission("submission-1")["policy_hash"] == "policy-hash"
    assert restarted.evaluation(CHALLENGE_ID)["matrix_hash"] == matrix["provenance"]["matrix_hash"]
    assert {event["action"] for event in restarted.audit_events(CHALLENGE_ID)} >= {
        "policy_submission",
        "hidden_evaluation",
        "hidden_release",
        "phase_transition",
    }


def test_experiment_job_claim_is_atomic_and_allows_failed_retry(tmp_path) -> None:
    store = ArenaStore(tmp_path / "job-claim.sqlite3")
    store.create_experiment_job("job-1", {"name": "retryable"}, "creator")
    progress = {"completed_cells": 0, "total_cells": 1, "percent": 0}

    assert store.claim_experiment_job("job-1", progress) is True
    assert store.claim_experiment_job("job-1", progress) is False
    store.update_experiment_job("job-1", status="failed", progress=progress)
    assert store.claim_experiment_job("job-1", progress) is True


def test_illegal_phase_transition_is_rejected(tmp_path) -> None:
    store = ArenaStore(tmp_path / "phase.sqlite3")
    store.ensure_default_challenge(CHALLENGE_ID, list(HIDDEN_VARIANTS))
    with pytest.raises(ValueError, match="illegal challenge transition"):
        store.transition(CHALLENGE_ID, "instructor", "released", "skip evaluation")


def test_transition_rejects_failed_compare_and_set_without_audit_or_history(tmp_path) -> None:
    store = ArenaStore(tmp_path / "transition-conflict.sqlite3")
    store.ensure_default_challenge(CHALLENGE_ID, list(HIDDEN_VARIANTS))
    with store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER ignore_submission_lock
            BEFORE UPDATE OF phase ON challenges
            WHEN OLD.challenge_id = 'trade-the-shock'
                 AND NEW.phase = 'submission_locked'
            BEGIN
                SELECT RAISE(IGNORE);
            END
            """
        )

    with pytest.raises(ArenaPhaseError, match="phase changed during transition"):
        store.transition(CHALLENGE_ID, "instructor", "submission_locked", "deadline")

    assert store.challenge(CHALLENGE_ID)["phase"] == "public_practice"
    with store.connection() as connection:
        history_count = connection.execute(
            "SELECT COUNT(*) FROM challenge_phases WHERE challenge_id = ?",
            (CHALLENGE_ID,),
        ).fetchone()[0]
    assert history_count == 1
    assert not any(event["action"] == "phase_transition" for event in store.audit_events(CHALLENGE_ID))


def test_practice_and_submission_writes_recheck_committed_phase(tmp_path) -> None:
    store = ArenaStore(tmp_path / "post-lock.sqlite3")
    store.ensure_default_challenge(CHALLENGE_ID, list(HIDDEN_VARIANTS))
    store.transition(CHALLENGE_ID, "instructor", "submission_locked", "deadline")

    with pytest.raises(ArenaPhaseError, match="no longer accepting"):
        store.save_practice(
            "practice-after-lock",
            CHALLENGE_ID,
            "student-one",
            "policy-hash",
            42,
            100.0,
            {"public_score": 100.0, "metrics": {}},
        )
    with pytest.raises(ArenaPhaseError, match="no longer accepting"):
        store.save_submission(
            "submission-after-lock",
            CHALLENGE_ID,
            "student-one",
            "1.0",
            policy_payload(),
            "policy-hash",
            {"public_score": 100.0, "metrics": {}},
        )

    assert store.practice_count(CHALLENGE_ID, "student-one") == 0
    assert store.submission_count(CHALLENGE_ID, "student-one") == 0
    actions = [event["action"] for event in store.audit_events(CHALLENGE_ID)]
    assert "public_practice" not in actions
    assert "policy_submission" not in actions


def test_submission_phase_race_returns_conflict_and_does_not_commit(client: TestClient, monkeypatch) -> None:
    api_module = importlib.import_module("app.api.app")
    original_run = api_module.run_policy_submission

    def lock_before_persistence(*args, **kwargs):
        store = ArenaStore()
        store.transition(CHALLENGE_ID, "race-instructor", "submission_locked", "deadline race")
        return original_run(*args, **kwargs)

    monkeypatch.setattr(api_module, "run_policy_submission", lock_before_persistence)
    response = client.post(
        f"/api/arena/execution/challenges/{CHALLENGE_ID}/submissions",
        headers={"X-Test-Role": "student", "X-Test-User": "race-student"},
        json={"policy": policy_payload()},
    )
    assert response.status_code == 409
    assert ArenaStore().submission_count(CHALLENGE_ID, "race-student") == 0


def test_evaluation_persistence_and_phase_transition_are_atomic_and_restart_safe(tmp_path) -> None:
    path = tmp_path / "atomic-evaluation.sqlite3"
    store = ArenaStore(path)
    store.ensure_default_challenge(CHALLENGE_ID, list(HIDDEN_VARIANTS))
    store.transition(CHALLENGE_ID, "instructor", "submission_locked", "deadline")
    matrix = {
        "provenance": {"matrix_hash": "a" * 64},
        "rows": [
            {
                "policy_id": "policy-one",
                "name": "Policy One",
                "public_score": 101.0,
                "public_rank": 1,
                "world_results": [
                    {
                        "variant": "normal",
                        "world_id": "normal-seed-42",
                        "world_hash": "b" * 64,
                        "seed": 42,
                        "metrics": {"completion_pct": 100.0},
                    }
                ],
            }
        ],
    }
    malformed = copy.deepcopy(matrix)
    del malformed["rows"][0]["name"]
    with pytest.raises(KeyError, match="name"):
        store.save_evaluation_and_transition(
            CHALLENGE_ID, "instructor", malformed, "evaluate malformed matrix"
        )
    assert store.challenge(CHALLENGE_ID)["phase"] == "submission_locked"
    with pytest.raises(KeyError):
        store.evaluation(CHALLENGE_ID)
    assert "hidden_evaluation" not in {event["action"] for event in store.audit_events(CHALLENGE_ID)}

    evaluation = store.save_evaluation_and_transition(
        CHALLENGE_ID, "instructor", matrix, "evaluate frozen matrix"
    )
    restarted = ArenaStore(path)
    assert restarted.challenge(CHALLENGE_ID)["phase"] == "hidden_evaluation"
    assert restarted.evaluation(CHALLENGE_ID)["evaluation_id"] == evaluation["evaluation_id"]
    evaluation_events = [
        event
        for event in restarted.audit_events(CHALLENGE_ID)
        if event["action"] in {"hidden_evaluation", "phase_transition"}
        and event["occurred_at"] == evaluation["initiated_at"]
    ]
    assert {event["action"] for event in evaluation_events} == {
        "hidden_evaluation",
        "phase_transition",
    }
    with restarted.connection() as connection:
        phase = connection.execute(
            "SELECT previous_state, new_state, reason FROM challenge_phases "
            "WHERE challenge_id = ? ORDER BY id DESC LIMIT 1",
            (CHALLENGE_ID,),
        ).fetchone()
        assert tuple(phase) == (
            "submission_locked",
            "hidden_evaluation",
            "evaluate frozen matrix",
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM evaluation_world_results WHERE evaluation_id = ?",
                (evaluation["evaluation_id"],),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM leaderboard_snapshots WHERE evaluation_id = ?",
                (evaluation["evaluation_id"],),
            ).fetchone()[0]
            == 2
        )


def test_concurrent_phase_and_final_submission_limits_are_atomic(tmp_path) -> None:
    store = ArenaStore(tmp_path / "concurrency.sqlite3")
    store.ensure_default_challenge(CHALLENGE_ID, list(HIDDEN_VARIANTS))

    def lock_once(index: int) -> str:
        try:
            return store.transition(CHALLENGE_ID, f"teacher-{index}", "submission_locked", "deadline")[
                "phase"
            ]
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lock_once, range(2)))
    assert sorted(outcomes) == ["rejected", "submission_locked"]
    phase_audits = [
        event for event in store.audit_events(CHALLENGE_ID) if event["action"] == "phase_transition"
    ]
    assert len(phase_audits) == 1

    fresh = ArenaStore(tmp_path / "submission-concurrency.sqlite3")
    fresh.ensure_default_challenge(CHALLENGE_ID, list(HIDDEN_VARIANTS))

    def submit_once(index: int) -> str:
        try:
            fresh.save_submission(
                f"submission-{index}",
                CHALLENGE_ID,
                "student-one",
                "1.0",
                policy_payload(),
                f"hash-{index}",
                {"public_score": 100.0 + index, "metrics": {"completion_pct": 100.0}},
                max_final_submissions=1,
            )
            return "saved"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit_once, range(2)))
    assert sorted(outcomes) == ["rejected", "saved"]
    assert len([row for row in fresh.submissions(CHALLENGE_ID) if row["status"] == "final"]) == 1
    submission_audits = [
        event for event in fresh.audit_events(CHALLENGE_ID) if event["action"] == "policy_submission"
    ]
    assert len(submission_audits) == 1

    practice_store = ArenaStore(tmp_path / "practice-concurrency.sqlite3")
    practice_store.ensure_default_challenge(CHALLENGE_ID, list(HIDDEN_VARIANTS))

    def practice_once(index: int) -> str:
        try:
            practice_store.save_practice(
                f"practice-{index}",
                CHALLENGE_ID,
                "student-one",
                f"hash-{index}",
                42,
                100.0,
                {"public_score": 100.0, "metrics": {"completion_pct": 100.0}},
                max_runs=1,
            )
            return "saved"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(practice_once, range(2)))
    assert sorted(outcomes) == ["rejected", "saved"]
    assert practice_store.practice_count(CHALLENGE_ID, "student-one") == 1
    practice_audits = [
        event for event in practice_store.audit_events(CHALLENGE_ID) if event["action"] == "public_practice"
    ]
    assert len(practice_audits) == 1


def test_feedback_audit_uses_authenticated_actor(tmp_path) -> None:
    store = ArenaStore(tmp_path / "feedback-actor.sqlite3")
    store.ensure_default_challenge(CHALLENGE_ID, list(HIDDEN_VARIANTS))
    store.save_submission(
        "feedback-submission",
        CHALLENGE_ID,
        "learner",
        "1.0",
        policy_payload(),
        "policy-hash",
        {"public_score": 100.0, "metrics": {}},
    )
    store.save_feedback(
        "feedback-submission",
        "teacher",
        "complete",
        None,
        {"status": "complete"},
    )
    event = next(
        event for event in store.audit_events(CHALLENGE_ID) if event["action"] == "feedback_report_saved"
    )
    assert event["actor"] == "teacher"
    assert event["details"]["submission_owner"] == "learner"


def test_challenge_designer_is_instructor_only_and_persists_a_draft(client: TestClient, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    payload = {
        "course_level": "graduate finance",
        "learning_objective": "Explain how execution controls behave under protected conditions.",
        "exchange_capabilities": ["price time priority", "explicit latency lifecycle"],
        "allowed_world_interventions": ["liquidity_withdrawal", "latency_shock"],
        "allowed_policy_parameters": [
            "target_participation",
            "max_participation",
            "feed_latency_tolerance_ms",
        ],
        "difficulty": "advanced",
    }
    route = "/api/arena/execution/challenge-designs"
    student_response = client.post(route, headers={"X-Test-Role": "student"}, json={})
    assert student_response.status_code == 403
    assert not any(variant in student_response.text for variant in HIDDEN_VARIANTS)
    options_route = "/api/arena/execution/challenge-design-options"
    student_options = client.get(options_route, headers={"X-Test-Role": "student"})
    assert student_options.status_code == 403
    assert not any(variant in student_options.text for variant in HIDDEN_VARIANTS)
    options = client.get(
        options_route,
        headers={"X-Test-Role": "instructor", "X-Test-User": "teacher"},
    )
    assert options.status_code == 200
    assert {item["id"] for item in options.json()["allowed_world_interventions"]} == set(HIDDEN_VARIANTS)
    response = client.post(
        route,
        headers={"X-Test-Role": "instructor", "X-Test-User": "teacher"},
        json=payload,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["approval_status"] == "draft"
    assert body["numeric_worlds_created"] is False
    assert body["mode"] == "deterministic_fallback"
    assert body["design_id"].startswith("design-")
    assert "challenge_design_draft" in {event["action"] for event in ArenaStore().audit_events(CHALLENGE_ID)}


def test_signed_demo_session_survives_restart_and_normal_role_header_is_ignored(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ARENA_DB_PATH", str(tmp_path / "sessions.sqlite3"))
    monkeypatch.setenv("ARENA_DEMO_AUTH", "1")
    monkeypatch.setenv("ARENA_DEMO_INSTRUCTOR_CODE", "test-instructor-code")
    monkeypatch.delenv("ARENA_TEST_AUTH", raising=False)
    client = TestClient(app)
    anonymous = client.get("/api/arena/session")
    assert anonymous.status_code == 200
    assert anonymous.json() == {"status": "anonymous", "authenticated": False}
    issued = client.post(
        "/api/arena/demo-session",
        json={
            "role": "instructor",
            "instructor_code": "test-instructor-code",
        },
    )
    assert issued.status_code == 200
    assert client.get("/api/arena/session").json() == {
        "status": "ok",
        "role": "instructor",
        "user_id": issued.json()["user_id"],
        "authentication": "demo_session",
        "authenticated": True,
    }
    # A valid instructor reaches the evaluation-state check rather than the role check.
    assert client.get(f"/api/arena/execution/challenges/{CHALLENGE_ID}/evidence").status_code == 409
    assert (
        ArenaStore(tmp_path / "sessions.sqlite3").session(
            hashlib.sha256(client.cookies["arena_demo_session"].strip('"').encode()).hexdigest()
        )
        is not None
    )

    attacker = TestClient(app)
    assert (
        attacker.post("/api/arena/demo-session", json={"role": "student", "user_id": "victim"}).status_code
        == 422
    )
    assert (
        attacker.post(
            "/api/arena/demo-session",
            json={"role": "instructor", "instructor_code": "wrong-code"},
        ).status_code
        == 403
    )
    assert (
        attacker.get(
            f"/api/arena/execution/challenges/{CHALLENGE_ID}/evidence",
            headers={"X-Role": "instructor"},
        ).status_code
        == 401
    )
    assert (
        attacker.post(
            f"/api/arena/execution/challenges/{CHALLENGE_ID}/submissions",
            json={"policy": policy_payload()},
        ).status_code
        == 401
    )

    browser = TestClient(app)
    student_id = browser.post("/api/arena/demo-session", json={"role": "student"}).json()["user_id"]
    assert student_id.startswith("demo-student-")
    assert (
        browser.post(
            "/api/arena/demo-session",
            json={"role": "instructor", "instructor_code": "test-instructor-code"},
        ).status_code
        == 200
    )
    resumed = browser.post("/api/arena/demo-session", json={"role": "student"})
    assert resumed.status_code == 200
    assert resumed.json()["user_id"] == student_id


def test_release_changes_visibility_not_evaluation(client: TestClient) -> None:
    instructor = {"X-Test-Role": "instructor", "X-Test-User": "teacher"}
    student = {"X-Test-Role": "student", "X-Test-User": "learner"}
    submission = client.post(
        f"/api/arena/execution/challenges/{CHALLENGE_ID}/submissions",
        headers=student,
        json={"policy": policy_payload()},
    )
    assert submission.status_code == 200
    submission_id = submission.json()["submission_id"]
    submitted_public_score = submission.json()["public_score"]
    current = client.get(f"/api/arena/execution/challenges/{CHALLENGE_ID}/submissions/me", headers=student)
    assert current.status_code == 200
    assert current.json()["final"]["submission_id"] == submission_id
    assert (
        client.post(
            f"/api/arena/execution/submissions/{submission_id}/feedback",
            headers=student,
            json={},
        ).json()["status"]
        == "withheld"
    )
    assert client.get(
        f"/api/arena/execution/challenges/{CHALLENGE_ID}/leaderboard/hidden", headers=student
    ).status_code in {403, 409}
    assert (
        client.post(
            f"/api/arena/execution/challenges/{CHALLENGE_ID}/lock",
            headers=instructor,
            json={"reason": "submission deadline"},
        ).status_code
        == 200
    )
    evaluated = client.post(f"/api/arena/execution/challenges/{CHALLENGE_ID}/evaluate", headers=instructor)
    assert evaluated.status_code == 200
    before = client.get(
        f"/api/arena/execution/challenges/{CHALLENGE_ID}/evidence", headers=instructor
    ).json()["evaluation"]
    evaluated_rows = {row["policy_id"]: row for row in before["matrix"]["rows"]}
    assert submission_id in evaluated_rows
    assert evaluated_rows[submission_id]["public_score"] == submitted_public_score
    assert (
        client.get(
            f"/api/arena/execution/challenges/{CHALLENGE_ID}/leaderboard/hidden", headers=student
        ).status_code
        == 403
    )
    released = client.post(
        f"/api/arena/execution/challenges/{CHALLENGE_ID}/release",
        headers=instructor,
        json={"reason": "release final assessment"},
    )
    assert released.status_code == 200
    after = client.get(f"/api/arena/execution/challenges/{CHALLENGE_ID}/evidence", headers=instructor).json()[
        "evaluation"
    ]
    assert copy.deepcopy(before["matrix"]) == after["matrix"]
    student_hidden = client.get(
        f"/api/arena/execution/challenges/{CHALLENGE_ID}/leaderboard/hidden", headers=student
    )
    assert student_hidden.status_code == 200
    assert "world_results" not in student_hidden.text
    assert "world_hash" not in student_hidden.text
    released_submission = next(
        row for row in student_hidden.json()["rows"] if row["policy_id"] == submission_id
    )
    released_intents = released_submission["released_intent_aggregates"]
    assert set(released_intents) == {
        "directional_crowding",
        "message_latency",
        "scheduled_event",
        "thin_liquidity",
    }
    assert all(variant not in student_hidden.text for variant in HIDDEN_VARIANTS)
    assert (
        client.get(f"/api/arena/execution/submissions/{submission_id}", headers=student).json()[
            "hidden_results"
        ]
        == "released"
    )
    feedback = client.post(
        f"/api/arena/execution/submissions/{submission_id}/feedback",
        headers=instructor,
        json={},
    )
    assert feedback.status_code == 200
    assert feedback.json()["mode"] == "deterministic_fallback"
    assert feedback.json()["scoring_authority"] == "deterministic_engine"
    assert feedback.json()["evidence_scope"] == "released_aggregates_and_public_trace_ids"
    assert "message_latency" in feedback.text
    assert "order_entry_latency_ms" in feedback.text
    assert not any(variant in feedback.text for variant in HIDDEN_VARIANTS)
    feedback_audit = next(
        event
        for event in ArenaStore().audit_events(CHALLENGE_ID)
        if event["action"] == "feedback_report_saved"
    )
    assert feedback_audit["actor"] == "teacher"
    assert feedback_audit["details"]["submission_owner"] == "learner"
    recovered = client.post(
        f"/api/arena/execution/submissions/{submission_id}/feedback",
        headers=student,
        json={},
    )
    assert recovered.status_code == 200
    assert recovered.json()["report_id"] == feedback.json()["report_id"]
    assert recovered.json()["recovered_from_sqlite"] is True
    assert (
        client.get(f"/api/arena/execution/challenges/{CHALLENGE_ID}/evidence", headers=student).status_code
        == 403
    )


def test_second_release_returns_conflict_not_server_error(client: TestClient) -> None:
    instructor = {"X-Test-Role": "instructor", "X-Test-User": "teacher"}
    student = {"X-Test-Role": "student", "X-Test-User": "learner"}
    assert (
        client.post(
            f"/api/arena/execution/challenges/{CHALLENGE_ID}/submissions",
            headers=student,
            json={"policy": policy_payload()},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/arena/execution/challenges/{CHALLENGE_ID}/lock",
            headers=instructor,
            json={"reason": "deadline"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/arena/execution/challenges/{CHALLENGE_ID}/evaluate",
            headers=instructor,
        ).status_code
        == 200
    )
    first = client.post(
        f"/api/arena/execution/challenges/{CHALLENGE_ID}/release",
        headers=instructor,
        json={"reason": "publish results"},
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/arena/execution/challenges/{CHALLENGE_ID}/release",
        headers=instructor,
        json={"reason": "publish results again"},
    )
    assert second.status_code == 409
    assert "evaluated before release" in second.json()["detail"]


def test_release_challenge_rejects_failed_compare_and_set(tmp_path) -> None:
    store = ArenaStore(tmp_path / "release-conflict.sqlite3")
    store.ensure_default_challenge(CHALLENGE_ID, list(HIDDEN_VARIANTS))
    store.transition(CHALLENGE_ID, "instructor", "submission_locked", "deadline")
    store.transition(CHALLENGE_ID, "instructor", "hidden_evaluation", "evaluate")
    matrix = benchmark_matrix(seeds=(42,))
    store.save_evaluation(CHALLENGE_ID, "instructor", matrix)
    with store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER ignore_release
            BEFORE UPDATE OF phase ON challenges
            WHEN OLD.challenge_id = 'trade-the-shock'
                 AND NEW.phase = 'released'
            BEGIN
                SELECT RAISE(IGNORE);
            END
            """
        )

    with pytest.raises(ArenaPhaseError, match="phase changed during release"):
        store.release_challenge(CHALLENGE_ID, "instructor", "publish")

    assert store.challenge(CHALLENGE_ID)["phase"] == "hidden_evaluation"
    assert all(event["action"] != "hidden_release" for event in store.audit_events(CHALLENGE_ID))
