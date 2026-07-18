"""Restart-safe SQLite state for the execution challenge.

The simulator remains deterministic and stateless.  This store owns product
state: identities, lifecycle, immutable submissions/evaluations, release
visibility, feedback, leaderboard snapshots, and audit history.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PHASES = (
    "draft",
    "public_practice",
    "submission_locked",
    "hidden_evaluation",
    "released",
    "archived",
)
ALLOWED_TRANSITIONS = {
    "draft": "public_practice",
    "public_practice": "submission_locked",
    "submission_locked": "hidden_evaluation",
    "hidden_evaluation": "released",
    "released": "archived",
}


class ArenaPhaseError(ValueError):
    """The requested write is not valid in the challenge's committed phase."""


class ArenaQuotaError(ValueError):
    """A persisted per-user challenge quota has been exhausted."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ArenaStore:
    def __init__(self, path: str | Path | None = None) -> None:
        configured: str | Path = (
            path if path is not None else os.getenv("ARENA_DB_PATH", "artifacts/arena.sqlite3")
        )
        self.path = Path(configured).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY, role TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    role TEXT NOT NULL, issued_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );
                CREATE TABLE IF NOT EXISTS challenges (
                    challenge_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    phase TEXT NOT NULL, public_world_variant TEXT NOT NULL,
                    hidden_worlds_json TEXT NOT NULL, max_practice_runs INTEGER NOT NULL,
                    max_final_submissions INTEGER NOT NULL, practice_score_mode TEXT NOT NULL,
                    best_public_only INTEGER NOT NULL, hidden_final_only INTEGER NOT NULL,
                    raw_evidence_released INTEGER NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS challenge_phases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, challenge_id TEXT NOT NULL,
                    actor TEXT NOT NULL, occurred_at TEXT NOT NULL,
                    previous_state TEXT, new_state TEXT NOT NULL, reason TEXT NOT NULL,
                    FOREIGN KEY(challenge_id) REFERENCES challenges(challenge_id)
                );
                CREATE TABLE IF NOT EXISTS policy_submissions (
                    submission_id TEXT PRIMARY KEY, challenge_id TEXT NOT NULL,
                    user_id TEXT NOT NULL, policy_version TEXT NOT NULL,
                    policy_json TEXT NOT NULL, policy_hash TEXT NOT NULL,
                    public_result_json TEXT NOT NULL, public_score REAL NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(challenge_id) REFERENCES challenges(challenge_id)
                );
                CREATE TABLE IF NOT EXISTS practice_runs (
                    run_id TEXT PRIMARY KEY, challenge_id TEXT NOT NULL,
                    user_id TEXT NOT NULL, policy_hash TEXT NOT NULL,
                    seed INTEGER NOT NULL, public_score REAL NOT NULL,
                    result_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(challenge_id) REFERENCES challenges(challenge_id)
                );
                CREATE TABLE IF NOT EXISTS hidden_evaluations (
                    evaluation_id TEXT PRIMARY KEY, challenge_id TEXT NOT NULL,
                    initiated_by TEXT NOT NULL, initiated_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL, matrix_hash TEXT NOT NULL,
                    matrix_json TEXT NOT NULL, released_at TEXT,
                    UNIQUE(challenge_id, matrix_hash),
                    FOREIGN KEY(challenge_id) REFERENCES challenges(challenge_id)
                );
                CREATE TABLE IF NOT EXISTS evaluation_world_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, evaluation_id TEXT NOT NULL,
                    policy_id TEXT NOT NULL, world_id TEXT NOT NULL, world_hash TEXT NOT NULL,
                    seed INTEGER NOT NULL, metrics_json TEXT NOT NULL,
                    FOREIGN KEY(evaluation_id) REFERENCES hidden_evaluations(evaluation_id)
                );
                CREATE TABLE IF NOT EXISTS leaderboard_snapshots (
                    snapshot_id TEXT PRIMARY KEY, challenge_id TEXT NOT NULL,
                    evaluation_id TEXT NOT NULL, visibility TEXT NOT NULL,
                    rows_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(challenge_id) REFERENCES challenges(challenge_id)
                );
                CREATE TABLE IF NOT EXISTS feedback_reports (
                    report_id TEXT PRIMARY KEY, submission_id TEXT NOT NULL,
                    status TEXT NOT NULL, model TEXT, report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(submission_id) REFERENCES policy_submissions(submission_id)
                );
                CREATE TABLE IF NOT EXISTS challenge_designs (
                    design_id TEXT PRIMARY KEY, challenge_id TEXT NOT NULL,
                    actor TEXT NOT NULL, constraints_json TEXT NOT NULL,
                    status TEXT NOT NULL, model TEXT, result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(challenge_id) REFERENCES challenges(challenge_id)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, challenge_id TEXT,
                    actor TEXT NOT NULL, action TEXT NOT NULL, occurred_at TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS synthetic_worlds (
                    world_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    schema_version TEXT NOT NULL, status TEXT NOT NULL, created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS synthetic_world_versions (
                    world_id TEXT NOT NULL, version INTEGER NOT NULL, manifest_json TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(world_id, version), FOREIGN KEY(world_id) REFERENCES synthetic_worlds(world_id)
                );
                CREATE TABLE IF NOT EXISTS scenario_packs (
                    scenario_pack_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    base_world_id TEXT NOT NULL, schema_version TEXT NOT NULL, manifest_json TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL, status TEXT NOT NULL, created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(base_world_id) REFERENCES synthetic_worlds(world_id)
                );
                CREATE TABLE IF NOT EXISTS strategies (
                    strategy_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    strategy_type TEXT NOT NULL, builtin_policy_id TEXT, version_label TEXT NOT NULL,
                    intended_use TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stress_experiments (
                    experiment_id TEXT PRIMARY KEY, name TEXT NOT NULL, scenario_pack_id TEXT NOT NULL,
                    strategy_ids_json TEXT NOT NULL, seeds_json TEXT NOT NULL, status TEXT NOT NULL,
                    result_json TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiment_jobs (
                    job_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, status TEXT NOT NULL,
                    progress_json TEXT NOT NULL, experiment_id TEXT, created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(experiment_id) REFERENCES stress_experiments(experiment_id)
                );
                CREATE TABLE IF NOT EXISTS experiment_artifacts (
                    artifact_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, kind TEXT NOT NULL,
                    content_json TEXT NOT NULL, content_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(job_id, kind), FOREIGN KEY(job_id) REFERENCES experiment_jobs(job_id)
                );
                CREATE TABLE IF NOT EXISTS validation_reports (
                    report_id TEXT PRIMARY KEY, experiment_id TEXT UNIQUE NOT NULL,
                    report_json TEXT NOT NULL, report_hash TEXT NOT NULL,
                    created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(experiment_id) REFERENCES stress_experiments(experiment_id)
                );
                CREATE TABLE IF NOT EXISTS calibration_packs (
                    pack_id TEXT PRIMARY KEY, pack_json TEXT NOT NULL, checksum TEXT NOT NULL,
                    created_by TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS world_calibrations (
                    world_id TEXT PRIMARY KEY, pack_id TEXT NOT NULL, attached_at TEXT NOT NULL,
                    attached_by TEXT NOT NULL, FOREIGN KEY(world_id) REFERENCES synthetic_worlds(world_id),
                    FOREIGN KEY(pack_id) REFERENCES calibration_packs(pack_id)
                );
                CREATE TABLE IF NOT EXISTS calibration_runs (
                    calibration_run_id TEXT PRIMARY KEY, pack_id TEXT NOT NULL,
                    mode TEXT NOT NULL, result_json TEXT NOT NULL, result_hash TEXT NOT NULL,
                    created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(pack_id) REFERENCES calibration_packs(pack_id)
                );
                CREATE INDEX IF NOT EXISTS idx_submission_challenge_user
                    ON policy_submissions(challenge_id, user_id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_final_submission
                    ON policy_submissions(challenge_id, user_id) WHERE status = 'final';
                CREATE INDEX IF NOT EXISTS idx_practice_challenge_user
                    ON practice_runs(challenge_id, user_id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_evaluation_per_challenge
                    ON hidden_evaluations(challenge_id);
                CREATE INDEX IF NOT EXISTS idx_audit_challenge
                    ON audit_events(challenge_id, occurred_at);
                """
            )

    def ensure_default_challenge(self, challenge_id: str, hidden_worlds: list[str]) -> dict[str, Any]:
        now = utc_now()
        with self.connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO challenges
                (challenge_id, title, phase, public_world_variant, hidden_worlds_json,
                 max_practice_runs, max_final_submissions, practice_score_mode,
                 best_public_only, hidden_final_only, raw_evidence_released, created_at, updated_at)
                VALUES (?, ?, 'public_practice', 'normal', ?, 5, 1, 'exact', 1, 1, 0, ?, ?)
                """,
                (challenge_id, "Trade the Shock", json.dumps(hidden_worlds), now, now),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO challenge_phases
                (challenge_id, actor, occurred_at, previous_state, new_state, reason)
                SELECT ?, 'system', ?, 'draft', 'public_practice', 'seed default challenge'
                WHERE NOT EXISTS (SELECT 1 FROM challenge_phases WHERE challenge_id = ?)
                """,
                (challenge_id, now, challenge_id),
            )
        return self.challenge(challenge_id)

    def challenge(self, challenge_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM challenges WHERE challenge_id = ?", (challenge_id,)
            ).fetchone()
        if row is None:
            raise KeyError(challenge_id)
        value = dict(row)
        value["hidden_worlds"] = json.loads(value.pop("hidden_worlds_json"))
        for key in ("best_public_only", "hidden_final_only", "raw_evidence_released"):
            value[key] = bool(value[key])
        return value

    def upsert_user(self, user_id: str, role: str) -> None:
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO users(user_id, role, created_at) VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET role = excluded.role""",
                (user_id, role, utc_now()),
            )

    def save_session(
        self, session_hash: str, user_id: str, role: str, issued_at: str, expires_at: str
    ) -> None:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._upsert_user_in_transaction(connection, user_id, role)
            connection.execute(
                "INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, ?, ?)",
                (session_hash, user_id, role, issued_at, expires_at),
            )

    def session(self, session_hash: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_hash = ?", (session_hash,)
            ).fetchone()
        return dict(row) if row else None

    def audit(self, challenge_id: str | None, actor: str, action: str, details: dict[str, Any]) -> None:
        with self.connection() as connection:
            self._audit_in_transaction(connection, challenge_id, actor, action, details)

    @staticmethod
    def _audit_in_transaction(
        connection: sqlite3.Connection,
        challenge_id: str | None,
        actor: str,
        action: str,
        details: dict[str, Any],
        *,
        occurred_at: str | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO audit_events(challenge_id, actor, action, occurred_at, details_json) VALUES (?, ?, ?, ?, ?)",
            (
                challenge_id,
                actor,
                action,
                occurred_at or utc_now(),
                json.dumps(details, sort_keys=True),
            ),
        )

    @staticmethod
    def _upsert_user_in_transaction(connection: sqlite3.Connection, user_id: str, role: str) -> None:
        connection.execute(
            """INSERT INTO users(user_id, role, created_at) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET role = excluded.role""",
            (user_id, role, utc_now()),
        )

    @staticmethod
    def _require_public_practice_in_transaction(connection: sqlite3.Connection, challenge_id: str) -> None:
        challenge = connection.execute(
            "SELECT phase FROM challenges WHERE challenge_id = ?", (challenge_id,)
        ).fetchone()
        if challenge is None:
            raise KeyError(challenge_id)
        if challenge["phase"] != "public_practice":
            raise ArenaPhaseError("challenge is no longer accepting practice or submissions")

    def transition(self, challenge_id: str, actor: str, new_state: str, reason: str) -> dict[str, Any]:
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT phase FROM challenges WHERE challenge_id = ?", (challenge_id,)
            ).fetchone()
            if row is None:
                raise KeyError(challenge_id)
            previous = str(row["phase"])
            if new_state not in PHASES or ALLOWED_TRANSITIONS.get(previous) != new_state:
                raise ValueError(f"illegal challenge transition: {previous} -> {new_state}")
            updated = connection.execute(
                "UPDATE challenges SET phase = ?, updated_at = ? WHERE challenge_id = ? AND phase = ?",
                (new_state, now, challenge_id, previous),
            )
            if updated.rowcount != 1:
                raise ArenaPhaseError("challenge phase changed during transition")
            connection.execute(
                "INSERT INTO challenge_phases(challenge_id, actor, occurred_at, previous_state, new_state, reason) VALUES (?, ?, ?, ?, ?, ?)",
                (challenge_id, actor, now, previous, new_state, reason),
            )
            self._audit_in_transaction(
                connection,
                challenge_id,
                actor,
                "phase_transition",
                {"previous": previous, "new": new_state, "reason": reason},
                occurred_at=now,
            )
        return self.challenge(challenge_id)

    def practice_count(self, challenge_id: str, user_id: str) -> int:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM practice_runs WHERE challenge_id = ? AND user_id = ?",
                (challenge_id, user_id),
            ).fetchone()
        return int(row["count"])

    def save_practice(
        self,
        run_id: str,
        challenge_id: str,
        user_id: str,
        policy_hash: str,
        seed: int,
        public_score: float,
        result: dict[str, Any],
        max_runs: int = 5,
    ) -> None:
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_public_practice_in_transaction(connection, challenge_id)
            self._upsert_user_in_transaction(connection, user_id, "student")
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM practice_runs WHERE challenge_id = ? AND user_id = ?",
                (challenge_id, user_id),
            ).fetchone()
            if int(count["count"]) >= max_runs:
                raise ArenaQuotaError("public practice limit reached")
            connection.execute(
                "INSERT INTO practice_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    challenge_id,
                    user_id,
                    policy_hash,
                    seed,
                    public_score,
                    json.dumps(result),
                    now,
                ),
            )
            self._audit_in_transaction(
                connection,
                challenge_id,
                user_id,
                "public_practice",
                {"run_id": run_id, "seed": seed},
                occurred_at=now,
            )

    def submission_count(self, challenge_id: str, user_id: str) -> int:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM policy_submissions WHERE challenge_id = ? AND user_id = ? AND status = 'final'",
                (challenge_id, user_id),
            ).fetchone()
        return int(row["count"])

    def save_submission(
        self,
        submission_id: str,
        challenge_id: str,
        user_id: str,
        policy_version: str,
        policy: dict[str, Any],
        policy_hash: str,
        public_result: dict[str, Any],
        status: str = "final",
        max_final_submissions: int = 1,
    ) -> None:
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_public_practice_in_transaction(connection, challenge_id)
            self._upsert_user_in_transaction(connection, user_id, "student")
            if status == "final":
                count = connection.execute(
                    "SELECT COUNT(*) AS count FROM policy_submissions WHERE challenge_id = ? AND user_id = ? AND status = 'final'",
                    (challenge_id, user_id),
                ).fetchone()
                if int(count["count"]) >= max_final_submissions:
                    raise ArenaQuotaError("final submission limit reached")
            connection.execute(
                "INSERT INTO policy_submissions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    submission_id,
                    challenge_id,
                    user_id,
                    policy_version,
                    json.dumps(policy, sort_keys=True),
                    policy_hash,
                    json.dumps(public_result),
                    float(public_result["public_score"]),
                    status,
                    now,
                ),
            )
            self._audit_in_transaction(
                connection,
                challenge_id,
                user_id,
                "policy_submission",
                {"submission_id": submission_id, "status": status},
                occurred_at=now,
            )

    def submissions(self, challenge_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM policy_submissions WHERE challenge_id = ? ORDER BY created_at, submission_id",
                (challenge_id,),
            ).fetchall()
        return [self._submission_value(row) for row in rows]

    def submission(self, submission_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM policy_submissions WHERE submission_id = ?", (submission_id,)
            ).fetchone()
        if row is None:
            raise KeyError(submission_id)
        return self._submission_value(row)

    @staticmethod
    def _submission_value(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["policy"] = json.loads(value.pop("policy_json"))
        value["public_result"] = json.loads(value.pop("public_result_json"))
        return value

    def _save_evaluation_in_transaction(
        self,
        connection: sqlite3.Connection,
        challenge_id: str,
        actor: str,
        matrix: dict[str, Any],
        now: str,
    ) -> sqlite3.Row:
        matrix_hash = str(matrix["provenance"]["matrix_hash"])
        evaluation_id = f"evaluation-{matrix_hash[:16]}"
        existing = connection.execute(
            "SELECT * FROM hidden_evaluations WHERE challenge_id = ?", (challenge_id,)
        ).fetchone()
        if existing:
            if existing["matrix_hash"] != matrix_hash:
                raise ValueError("challenge already has a different hidden evaluation")
            return existing
        connection.execute(
            "INSERT INTO hidden_evaluations VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            (evaluation_id, challenge_id, actor, now, now, matrix_hash, json.dumps(matrix)),
        )
        for policy in matrix["rows"]:
            for world in policy.get("world_results", []):
                world_hash = str(world.get("world_hash") or world.get("specification_hash") or "withheld")
                connection.execute(
                    "INSERT INTO evaluation_world_results(evaluation_id, policy_id, world_id, world_hash, seed, metrics_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        evaluation_id,
                        policy["policy_id"],
                        str(world.get("world_id") or world["variant"]),
                        world_hash,
                        int(world["seed"]),
                        json.dumps(world["metrics"]),
                    ),
                )
        public_rows = [
            {key: row[key] for key in ("policy_id", "name", "public_score", "public_rank")}
            for row in matrix["rows"]
        ]
        connection.execute(
            "INSERT INTO leaderboard_snapshots VALUES (?, ?, ?, 'public', ?, ?)",
            (
                f"public-{matrix_hash[:16]}",
                challenge_id,
                evaluation_id,
                json.dumps(public_rows),
                now,
            ),
        )
        connection.execute(
            "INSERT INTO leaderboard_snapshots VALUES (?, ?, ?, 'hidden', ?, ?)",
            (
                f"hidden-{matrix_hash[:16]}",
                challenge_id,
                evaluation_id,
                json.dumps(matrix["rows"]),
                now,
            ),
        )
        self._audit_in_transaction(
            connection,
            challenge_id,
            actor,
            "hidden_evaluation",
            {"evaluation_id": evaluation_id, "matrix_hash": matrix_hash},
            occurred_at=now,
        )
        saved = connection.execute(
            "SELECT * FROM hidden_evaluations WHERE evaluation_id = ?", (evaluation_id,)
        ).fetchone()
        if saved is None:  # pragma: no cover - guarded by the insert above
            raise RuntimeError("hidden evaluation insert did not persist")
        return saved

    def save_evaluation(self, challenge_id: str, actor: str, matrix: dict[str, Any]) -> dict[str, Any]:
        """Persist an evaluation without changing lifecycle state.

        Kept for store-level tooling and backward compatibility. The assessment
        API uses ``save_evaluation_and_transition`` so persistence and lifecycle
        advancement share one transaction.
        """
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._save_evaluation_in_transaction(connection, challenge_id, actor, matrix, now)
        return self.evaluation(challenge_id)

    def save_evaluation_and_transition(
        self,
        challenge_id: str,
        actor: str,
        matrix: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        """Atomically persist hidden evidence and enter ``hidden_evaluation``."""
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            challenge = connection.execute(
                "SELECT phase FROM challenges WHERE challenge_id = ?", (challenge_id,)
            ).fetchone()
            if challenge is None:
                raise KeyError(challenge_id)
            previous = str(challenge["phase"])
            if previous != "submission_locked":
                raise ArenaPhaseError(f"illegal challenge transition: {previous} -> hidden_evaluation")
            self._save_evaluation_in_transaction(connection, challenge_id, actor, matrix, now)
            updated = connection.execute(
                "UPDATE challenges SET phase = 'hidden_evaluation', updated_at = ? "
                "WHERE challenge_id = ? AND phase = 'submission_locked'",
                (now, challenge_id),
            )
            if updated.rowcount != 1:  # pragma: no cover - BEGIN IMMEDIATE serializes writers
                raise ArenaPhaseError("challenge phase changed during hidden evaluation")
            connection.execute(
                "INSERT INTO challenge_phases(challenge_id, actor, occurred_at, previous_state, new_state, reason) "
                "VALUES (?, ?, ?, 'submission_locked', 'hidden_evaluation', ?)",
                (challenge_id, actor, now, reason),
            )
            self._audit_in_transaction(
                connection,
                challenge_id,
                actor,
                "phase_transition",
                {
                    "previous": "submission_locked",
                    "new": "hidden_evaluation",
                    "reason": reason,
                },
                occurred_at=now,
            )
        return self.evaluation(challenge_id)

    def evaluation(self, challenge_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM hidden_evaluations WHERE challenge_id = ?", (challenge_id,)
            ).fetchone()
        if row is None:
            raise KeyError(challenge_id)
        return self._evaluation_value(row)

    @staticmethod
    def _evaluation_value(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["matrix"] = json.loads(value.pop("matrix_json"))
        return value

    def release_evaluation(self, challenge_id: str, actor: str) -> dict[str, Any]:
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            evaluation = connection.execute(
                "SELECT * FROM hidden_evaluations WHERE challenge_id = ?", (challenge_id,)
            ).fetchone()
            if evaluation is None:
                raise KeyError(challenge_id)
            released_at = evaluation["released_at"] or now
            connection.execute(
                "UPDATE hidden_evaluations SET released_at = ? WHERE challenge_id = ?",
                (released_at, challenge_id),
            )
            self._audit_in_transaction(
                connection,
                challenge_id,
                actor,
                "hidden_release",
                {"evaluation_id": evaluation["evaluation_id"]},
                occurred_at=now,
            )
        return self.evaluation(challenge_id)

    def release_challenge(self, challenge_id: str, actor: str, reason: str) -> dict[str, Any]:
        """Atomically release the stored evaluation and advance challenge visibility."""
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            challenge = connection.execute(
                "SELECT phase FROM challenges WHERE challenge_id = ?", (challenge_id,)
            ).fetchone()
            evaluation = connection.execute(
                "SELECT evaluation_id FROM hidden_evaluations WHERE challenge_id = ?",
                (challenge_id,),
            ).fetchone()
            if challenge is None:
                raise KeyError(challenge_id)
            if challenge["phase"] != "hidden_evaluation":
                raise ArenaPhaseError("challenge must be evaluated before release")
            if evaluation is None:
                raise ValueError("hidden evaluation is missing")
            connection.execute(
                "UPDATE hidden_evaluations SET released_at = COALESCE(released_at, ?) WHERE challenge_id = ?",
                (now, challenge_id),
            )
            updated = connection.execute(
                "UPDATE challenges SET phase = 'released', updated_at = ? WHERE challenge_id = ? AND phase = ?",
                (now, challenge_id, "hidden_evaluation"),
            )
            if updated.rowcount != 1:
                raise ArenaPhaseError("challenge phase changed during release")
            connection.execute(
                "INSERT INTO challenge_phases(challenge_id, actor, occurred_at, previous_state, new_state, reason) VALUES (?, ?, ?, 'hidden_evaluation', 'released', ?)",
                (challenge_id, actor, now, reason),
            )
            connection.execute(
                "INSERT INTO audit_events(challenge_id, actor, action, occurred_at, details_json) VALUES (?, ?, 'hidden_release', ?, ?)",
                (
                    challenge_id,
                    actor,
                    now,
                    json.dumps({"evaluation_id": evaluation["evaluation_id"], "reason": reason}),
                ),
            )
        return self.evaluation(challenge_id)

    def save_feedback(
        self,
        submission_id: str,
        actor: str,
        status: str,
        model: str | None,
        report: dict[str, Any],
    ) -> str:
        report_id = f"feedback-{submission_id}"
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR REPLACE INTO feedback_reports VALUES (?, ?, ?, ?, ?, ?)",
                (report_id, submission_id, status, model, json.dumps(report), now),
            )
            submission = connection.execute(
                "SELECT challenge_id, user_id FROM policy_submissions WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
            if submission is not None:
                self._audit_in_transaction(
                    connection,
                    submission["challenge_id"],
                    actor,
                    "feedback_report_saved",
                    {
                        "report_id": report_id,
                        "submission_id": submission_id,
                        "submission_owner": submission["user_id"],
                        "status": status,
                    },
                    occurred_at=now,
                )
        return report_id

    def feedback(self, submission_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM feedback_reports WHERE submission_id = ?", (submission_id,)
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["report"] = json.loads(value.pop("report_json"))
        return value

    def save_challenge_design(
        self,
        challenge_id: str,
        actor: str,
        constraints: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        """Persist a qualitative draft; this never creates or mutates numeric worlds."""
        payload = json.dumps(
            {"constraints": constraints, "result": result},
            sort_keys=True,
            separators=(",", ":"),
        )
        design_id = f"design-{hashlib.sha256(payload.encode()).hexdigest()[:16]}"
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO challenge_designs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    design_id,
                    challenge_id,
                    actor,
                    json.dumps(constraints, sort_keys=True),
                    str(result["status"]),
                    result.get("model"),
                    json.dumps(result, sort_keys=True),
                    now,
                ),
            )
            self._audit_in_transaction(
                connection,
                challenge_id,
                actor,
                "challenge_design_draft",
                {"design_id": design_id, "status": result["status"], "mode": result["mode"]},
                occurred_at=now,
            )
        return design_id

    def audit_events(self, challenge_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE challenge_id = ? ORDER BY id", (challenge_id,)
            ).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            value["details"] = json.loads(value.pop("details_json"))
            values.append(value)
        return values

    def create_synthetic_world(
        self, world_id: str, payload: dict[str, Any], actor: str, manifest_hash: str
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO synthetic_worlds VALUES (?, ?, ?, ?, 'draft', ?, ?, ?)",
                (
                    world_id,
                    payload["name"],
                    payload["description"],
                    payload["schema_version"],
                    actor,
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO synthetic_world_versions VALUES (?, 1, ?, ?, ?)",
                (world_id, json.dumps(payload, sort_keys=True), manifest_hash, now),
            )
            self._audit_in_transaction(
                connection,
                None,
                actor,
                "synthetic_world_created",
                {"world_id": world_id, "manifest_hash": manifest_hash},
                occurred_at=now,
            )
        return self.synthetic_world(world_id)

    def synthetic_world(self, world_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            world = connection.execute(
                "SELECT * FROM synthetic_worlds WHERE world_id = ?", (world_id,)
            ).fetchone()
            version = connection.execute(
                "SELECT * FROM synthetic_world_versions WHERE world_id = ? ORDER BY version DESC LIMIT 1",
                (world_id,),
            ).fetchone()
        if world is None or version is None:
            raise KeyError(world_id)
        value = dict(world)
        value["version"] = int(version["version"])
        value["manifest"] = json.loads(version["manifest_json"])
        value["manifest_hash"] = version["manifest_hash"]
        return value

    def synthetic_worlds(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT world_id FROM synthetic_worlds ORDER BY created_at DESC"
            ).fetchall()
        return [self.synthetic_world(str(row["world_id"])) for row in rows]

    def attach_calibration_pack(
        self,
        world_id: str,
        pack: dict[str, Any],
        actor: str,
        calibration_run: dict[str, Any],
        calibration_run_id: str,
    ) -> dict[str, Any]:
        now = utc_now()
        payload = json.dumps(pack, sort_keys=True, separators=(",", ":"))
        checksum = str(pack["checksum"])
        run_payload = json.dumps(calibration_run, sort_keys=True, separators=(",", ":"))
        run_hash = hashlib.sha256(run_payload.encode()).hexdigest()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            world = connection.execute(
                "SELECT 1 FROM synthetic_worlds WHERE world_id = ?", (world_id,)
            ).fetchone()
            if world is None:
                raise KeyError(world_id)
            connection.execute(
                "INSERT OR IGNORE INTO calibration_packs VALUES (?, ?, ?, ?, ?)",
                (pack["pack_id"], payload, checksum, actor, now),
            )
            connection.execute(
                "INSERT OR IGNORE INTO calibration_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    calibration_run_id,
                    pack["pack_id"],
                    calibration_run["mode"],
                    run_payload,
                    run_hash,
                    actor,
                    now,
                ),
            )
            latest = connection.execute(
                "SELECT version, manifest_json FROM synthetic_world_versions WHERE world_id = ? ORDER BY version DESC LIMIT 1",
                (world_id,),
            ).fetchone()
            if latest is None:
                raise KeyError(world_id)
            manifest = json.loads(latest["manifest_json"])
            manifest["calibration_pack_id"] = pack["pack_id"]
            manifest["calibration_checksum"] = checksum
            manifest["calibration_run_id"] = calibration_run_id
            manifest["calibration_stable"] = bool(calibration_run["heldout_stability"]["stable"])
            manifest["updated_at"] = now
            manifest_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
            connection.execute(
                "INSERT INTO synthetic_world_versions VALUES (?, ?, ?, ?, ?)",
                (
                    world_id,
                    int(latest["version"]) + 1,
                    json.dumps(manifest, sort_keys=True),
                    manifest_hash,
                    now,
                ),
            )
            connection.execute(
                "INSERT OR REPLACE INTO world_calibrations VALUES (?, ?, ?, ?)",
                (world_id, pack["pack_id"], now, actor),
            )
            self._audit_in_transaction(
                connection,
                None,
                actor,
                "calibration_pack_attached",
                {
                    "world_id": world_id,
                    "pack_id": pack["pack_id"],
                    "calibration_run_id": calibration_run_id,
                    "manifest_hash": manifest_hash,
                },
                occurred_at=now,
            )
        return self.synthetic_world(world_id)

    def calibration_pack(self, pack_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM calibration_packs WHERE pack_id = ?", (pack_id,)
            ).fetchone()
        if row is None:
            raise KeyError(pack_id)
        value = dict(row)
        value["pack"] = json.loads(value.pop("pack_json"))
        return value

    def calibration_run(self, calibration_run_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM calibration_runs WHERE calibration_run_id = ?", (calibration_run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(calibration_run_id)
        value = dict(row)
        value["result"] = json.loads(value.pop("result_json"))
        return value

    def create_scenario_pack(
        self, scenario_pack_id: str, payload: dict[str, Any], actor: str, manifest_hash: str
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM synthetic_worlds WHERE world_id = ?", (payload["base_world_id"],)
            ).fetchone()
            if exists is None:
                raise KeyError(payload["base_world_id"])
            connection.execute(
                "INSERT INTO scenario_packs VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)",
                (
                    scenario_pack_id,
                    payload["name"],
                    payload["description"],
                    payload["base_world_id"],
                    payload["schema_version"],
                    json.dumps(payload, sort_keys=True),
                    manifest_hash,
                    actor,
                    now,
                    now,
                ),
            )
            self._audit_in_transaction(
                connection,
                None,
                actor,
                "scenario_pack_created",
                {"scenario_pack_id": scenario_pack_id, "manifest_hash": manifest_hash},
                occurred_at=now,
            )
        return self.scenario_pack(scenario_pack_id)

    def scenario_pack(self, scenario_pack_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM scenario_packs WHERE scenario_pack_id = ?", (scenario_pack_id,)
            ).fetchone()
        if row is None:
            raise KeyError(scenario_pack_id)
        value = dict(row)
        value["manifest"] = json.loads(value.pop("manifest_json"))
        return value

    def scenario_packs(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT scenario_pack_id FROM scenario_packs ORDER BY created_at DESC"
            ).fetchall()
        return [self.scenario_pack(str(row["scenario_pack_id"])) for row in rows]

    def create_strategy(self, strategy_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        now = utc_now()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO strategies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    strategy_id,
                    payload["name"],
                    payload["description"],
                    payload["strategy_type"],
                    payload.get("builtin_policy_id"),
                    payload["version_label"],
                    payload["intended_use"],
                    actor,
                    now,
                    now,
                ),
            )
            self._audit_in_transaction(
                connection, None, actor, "strategy_registered", {"strategy_id": strategy_id}, occurred_at=now
            )
        return self.strategy(strategy_id)

    def strategy(self, strategy_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM strategies WHERE strategy_id = ?", (strategy_id,)
            ).fetchone()
        if row is None:
            raise KeyError(strategy_id)
        return dict(row)

    def strategies(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM strategies ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def save_stress_experiment(
        self, experiment_id: str, payload: dict[str, Any], actor: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO stress_experiments VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?)",
                (
                    experiment_id,
                    payload["name"],
                    payload["scenario_pack_id"],
                    json.dumps(payload["strategy_ids"]),
                    json.dumps(payload["seeds"]),
                    json.dumps(result, sort_keys=True),
                    actor,
                    now,
                    now,
                ),
            )
            self._audit_in_transaction(
                connection,
                None,
                actor,
                "stress_experiment_completed",
                {"experiment_id": experiment_id},
                occurred_at=now,
            )
        return self.stress_experiment(experiment_id)

    def stress_experiment(self, experiment_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM stress_experiments WHERE experiment_id = ?", (experiment_id,)
            ).fetchone()
        if row is None:
            raise KeyError(experiment_id)
        value = dict(row)
        value["strategy_ids"] = json.loads(value.pop("strategy_ids_json"))
        value["seeds"] = json.loads(value.pop("seeds_json"))
        value["result"] = json.loads(value.pop("result_json")) if value.get("result_json") else None
        value.pop("result_json", None)
        return value

    def stress_experiments(
        self, *, limit: int = 50, offset: int = 0, include_results: bool = False
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM stress_experiments ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        results = []
        for row in rows:
            value = dict(row)
            value["strategy_ids"] = json.loads(value.pop("strategy_ids_json"))
            value["seeds"] = json.loads(value.pop("seeds_json"))
            result_json = value.pop("result_json")
            if include_results:
                value["result"] = json.loads(result_json) if result_json else None
            else:
                value["has_result"] = bool(result_json)
            results.append(value)
        return results

    def create_experiment_job(self, job_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        now = utc_now()
        progress = {"completed_cells": 0, "total_cells": 0, "percent": 0}
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO experiment_jobs VALUES (?, ?, 'queued', ?, NULL, ?, ?, ?)",
                (job_id, json.dumps(payload, sort_keys=True), json.dumps(progress), actor, now, now),
            )
        return self.experiment_job(job_id)

    def update_experiment_job(
        self, job_id: str, *, status: str, progress: dict[str, Any], experiment_id: str | None = None
    ) -> dict[str, Any]:
        with self.connection() as connection:
            updated = connection.execute(
                "UPDATE experiment_jobs SET status = ?, progress_json = ?, experiment_id = COALESCE(?, experiment_id), updated_at = ? WHERE job_id = ?",
                (status, json.dumps(progress, sort_keys=True), experiment_id, utc_now(), job_id),
            )
        if updated.rowcount != 1:
            raise KeyError(job_id)
        return self.experiment_job(job_id)

    def experiment_job(self, job_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM experiment_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        value["progress"] = json.loads(value.pop("progress_json"))
        return value

    def experiment_jobs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT job_id FROM experiment_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self.experiment_job(str(row["job_id"])) for row in rows]

    def save_experiment_artifact(
        self, artifact_id: str, job_id: str, kind: str, content: dict[str, Any]
    ) -> dict[str, Any]:
        encoded = json.dumps(content, sort_keys=True, separators=(",", ":"))
        record = {
            "artifact_id": artifact_id,
            "job_id": job_id,
            "kind": kind,
            "content": content,
            "content_hash": hashlib.sha256(encoded.encode()).hexdigest(),
            "created_at": utc_now(),
        }
        with self.connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO experiment_artifacts VALUES (?, ?, ?, ?, ?, ?)",
                (artifact_id, job_id, kind, encoded, record["content_hash"], record["created_at"]),
            )
        return record

    def experiment_artifact(self, job_id: str, kind: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM experiment_artifacts WHERE job_id = ? AND kind = ?", (job_id, kind)
            ).fetchone()
        if row is None:
            raise KeyError((job_id, kind))
        value = dict(row)
        value["content"] = json.loads(value.pop("content_json"))
        return value

    def save_validation_report(
        self, report_id: str, experiment_id: str, report: dict[str, Any], actor: str
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO validation_reports VALUES (?, ?, ?, ?, ?, ?)",
                (
                    report_id,
                    experiment_id,
                    json.dumps(report, sort_keys=True),
                    report["report_hash"],
                    actor,
                    now,
                ),
            )
            self._audit_in_transaction(
                connection,
                None,
                actor,
                "validation_report_saved",
                {"report_id": report_id, "experiment_id": experiment_id},
                occurred_at=now,
            )
        return self.validation_report(experiment_id)

    def validation_report(self, experiment_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM validation_reports WHERE experiment_id = ?", (experiment_id,)
            ).fetchone()
        if row is None:
            raise KeyError(experiment_id)
        value = dict(row)
        value["report"] = json.loads(value.pop("report_json"))
        return value
