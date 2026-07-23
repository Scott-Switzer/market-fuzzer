from __future__ import annotations

import hashlib
import json
from datetime import UTC
from typing import Any
from uuid import uuid4

from app.execution_store import ArenaStore


class StrategyLabRepository:
    def __init__(self, store: ArenaStore) -> None:
        self.store = store

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid4().hex[:12]}"

    @staticmethod
    def _now() -> str:
        from datetime import datetime

        return datetime.now(UTC).isoformat()

    def initialize(self) -> None:
        from app.execution_store import StrategyLabStore

        StrategyLabStore(self.store).initialize()

    def _ensure_initialized(self) -> None:
        with self.store.connection() as connection:
            initialized = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_lab_migrations'"
            ).fetchone()
        if initialized is None:
            self.initialize()

    def save_strategy_version(self, payload: dict[str, Any]) -> dict[str, Any]:
        strategy_id = str(payload.get("strategy_id") or self._new_id("strategy"))
        now = self._now()
        created_at = str(payload.get("created_at") or now)
        updated_at = str(payload.get("updated_at") or now)
        canonical_hash = str(payload.get("canonical_hash") or "")
        spec = dict(payload.get("spec") or payload)
        spec_json = json.dumps(spec, sort_keys=True, separators=(",", ":"))
        if not canonical_hash:
            canonical_hash = hashlib.sha256(spec_json.encode()).hexdigest()
        row = {
            "strategy_id": strategy_id,
            "name": spec.get("name") or payload.get("name") or strategy_id,
            "description": spec.get("description") or payload.get("description") or "",
            "strategy_type": spec.get("strategy_type") or payload.get("strategy_type") or "unknown",
            "builtin_policy_id": spec.get("builtin_policy_id") or payload.get("builtin_policy_id"),
            "version_label": spec.get("version_label") or payload.get("version_label") or "v1",
            "intended_use": spec.get("intended_use") or payload.get("intended_use") or "general",
            "created_by": str(payload.get("created_by") or "system"),
            "created_at": created_at,
            "updated_at": updated_at,
            "canonical_hash": canonical_hash,
            "spec_json": spec_json,
        }
        with self.store.connection() as connection:
            existing = connection.execute(
                "SELECT spec_json FROM strategy_versions WHERE strategy_id = ?", (row["strategy_id"],)
            ).fetchone()
            if existing:
                existing_spec = json.loads(str(existing["spec_json"]))
                if existing_spec.get("is_locked"):
                    raise ValueError(
                        f"Strategy version {row['strategy_id']} is locked and cannot be modified."
                    )

            connection.execute(
                """
                INSERT INTO strategies
                    (strategy_id, name, description, strategy_type, builtin_policy_id,
                     version_label, intended_use, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    strategy_type=excluded.strategy_type,
                    builtin_policy_id=excluded.builtin_policy_id,
                    version_label=excluded.version_label,
                    intended_use=excluded.intended_use,
                    updated_at=excluded.updated_at
                """,
                (
                    row["strategy_id"],
                    row["name"],
                    row["description"],
                    row["strategy_type"],
                    row["builtin_policy_id"],
                    row["version_label"],
                    row["intended_use"],
                    row["created_by"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
            connection.execute(
                """
                INSERT INTO strategy_versions VALUES (
                    :strategy_id, :name, :description, :strategy_type,
                    :builtin_policy_id, :version_label, :intended_use,
                    :created_by, :created_at, :updated_at, :canonical_hash, :spec_json
                )
                ON CONFLICT(strategy_id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    strategy_type=excluded.strategy_type,
                    builtin_policy_id=excluded.builtin_policy_id,
                    version_label=excluded.version_label,
                    intended_use=excluded.intended_use,
                    updated_at=excluded.updated_at,
                    canonical_hash=excluded.canonical_hash,
                    spec_json=excluded.spec_json
                """,
                row,
            )
        return {**row, "spec": spec}

    def strategy_version(self, strategy_id: str) -> dict[str, Any]:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM strategy_versions WHERE strategy_id = ?", (strategy_id,)
            ).fetchone()
        if row is None:
            raise KeyError(strategy_id)
        value = dict(row)
        value["spec"] = json.loads(str(value.pop("spec_json")))
        return value

    def strategy_versions(self) -> list[dict[str, Any]]:
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT strategy_id FROM strategy_versions ORDER BY created_at DESC"
            ).fetchall()
        return [self.strategy_version(str(row["strategy_id"])) for row in rows]

    def save_clause(self, payload: dict[str, Any]) -> dict[str, Any]:
        clause_id = str(payload.get("clause_id") or self._new_id("clause"))
        now = self._now()
        row = {
            "clause_id": clause_id,
            "strategy_id": str(payload["strategy_id"]),
            "order_index": int(payload.get("order_index") or 0),
            "kind": str(payload.get("kind") or "custom"),
            "clause_json": json.dumps(
                payload.get("clause") or payload, sort_keys=True, separators=(",", ":")
            ),
            "original_text": str(payload.get("original_text") or ""),
            "normalized_text": payload.get("normalized_text"),
            "status": str(payload.get("status") or "pending"),
            "reason": payload.get("reason"),
            "user_resolution": str(payload.get("user_resolution") or "pending"),
            "compiler_confidence": payload.get("compiler_confidence"),
            "provenance_json": json.dumps(payload.get("provenance") or {}, sort_keys=True),
            "created_at": str(payload.get("created_at") or now),
        }
        with self.store.connection() as connection:
            connection.execute(
                """
                INSERT INTO strategy_clauses VALUES (
                    :clause_id, :strategy_id, :order_index, :kind, :clause_json,
                    :original_text, :normalized_text, :status, :reason, :user_resolution,
                    :compiler_confidence, :provenance_json, :created_at
                )
                """,
                row,
            )
        return {**row, "clause": json.loads(str(row["clause_json"]))}

    def clauses_for_strategy(self, strategy_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM strategy_clauses WHERE strategy_id = ? ORDER BY order_index, clause_id",
                (strategy_id,),
            ).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            value["clause"] = json.loads(str(value.pop("clause_json")))
            value["provenance"] = json.loads(str(value.pop("provenance_json")))
            values.append(value)
        return values

    def save_approval(self, payload: dict[str, Any]) -> dict[str, Any]:
        approval_id = str(payload.get("approval_id") or self._new_id("approval"))
        now = self._now()
        row = {
            "approval_id": approval_id,
            "strategy_id": str(payload["strategy_id"]),
            "status": str(payload.get("status") or "approved"),
            "approved_at": str(payload.get("approved_at") or now),
            "approved_by": str(payload.get("approved_by") or "system"),
            "canonical_hash": str(payload.get("canonical_hash") or ""),
            "approval_json": json.dumps(
                payload.get("approval") or payload, sort_keys=True, separators=(",", ":")
            ),
        }
        if not row["canonical_hash"]:
            row["canonical_hash"] = hashlib.sha256(row["approval_json"].encode()).hexdigest()
        with self.store.connection() as connection:
            connection.execute(
                "INSERT INTO strategy_approvals VALUES (:approval_id, :strategy_id, :status, :approved_at, :approved_by, :canonical_hash, :approval_json)",
                row,
            )
        return {**row, "approval": json.loads(str(row["approval_json"]))}

    def approvals_for_strategy(self, strategy_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM strategy_approvals WHERE strategy_id = ? ORDER BY approved_at, approval_id",
                (strategy_id,),
            ).fetchall()
        return [{**dict(row), "approval": json.loads(str(row["approval_json"]))} for row in rows]

    def save_backtest(self, payload: dict[str, Any]) -> dict[str, Any]:
        backtest_id = str(payload.get("backtest_id") or self._new_id("backtest"))
        now = self._now()
        result = payload.get("result") or {}
        metrics = payload.get("metrics") or {}
        failure_ids = payload.get("failure_ids") or []
        result_json = json.dumps(result, sort_keys=True, separators=(",", ":"))
        result_hash = hashlib.sha256(result_json.encode()).hexdigest()
        row = {
            "backtest_id": backtest_id,
            "strategy_id": str(payload["strategy_id"]),
            "session_id": str(payload.get("session_id") or backtest_id),
            "status": str(payload.get("status") or "completed"),
            "result_json": result_json,
            "result_hash": result_hash,
            "metrics_json": json.dumps(metrics, sort_keys=True),
            "failure_ids_json": json.dumps(failure_ids, sort_keys=True),
            "created_at": str(payload.get("created_at") or now),
            "updated_at": str(payload.get("updated_at") or now),
        }
        with self.store.connection() as connection:
            connection.execute(
                """
                INSERT INTO strategy_backtests VALUES (
                    :backtest_id, :strategy_id, :session_id, :status, :result_json,
                    :result_hash, :metrics_json, :failure_ids_json, :created_at, :updated_at
                )
                """,
                row,
            )
        return {**row, "result": result, "metrics": metrics, "failure_ids": failure_ids}

    def backtests_for_strategy(self, strategy_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM strategy_backtests WHERE strategy_id = ? ORDER BY created_at DESC, backtest_id",
                (strategy_id,),
            ).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            value["result"] = json.loads(str(value.pop("result_json")))
            value["metrics"] = json.loads(str(value.pop("metrics_json")))
            value["failure_ids"] = json.loads(str(value.pop("failure_ids_json")))
            values.append(value)
        return values

    def save_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        campaign_id = str(payload.get("campaign_id") or self._new_id("campaign"))
        now = self._now()
        row = {
            "campaign_id": campaign_id,
            "strategy_id": str(payload["strategy_id"]),
            "state": str(payload.get("state") or "prepared"),
            "commitment_digest": payload.get("commitment_digest"),
            "artifact_digest": payload.get("artifact_digest"),
            "artifact_byte_length": payload.get("artifact_byte_length"),
            "public_document_json": json.dumps(payload.get("public_document") or {}, sort_keys=True),
            "policy_json": json.dumps(payload.get("policy") or {}, sort_keys=True),
            "generator_bundle_digest": str(payload.get("generator_bundle_digest") or ""),
            "secret_seed_material_hex": str(payload.get("secret_seed_material_hex") or ""),
            "instruments_json": json.dumps(payload.get("instruments") or [], sort_keys=True),
            "steps": int(payload.get("steps") or 0),
            "result_json": json.dumps(payload.get("result") or {}, sort_keys=True)
            if payload.get("result")
            else None,
            "created_at": str(payload.get("created_at") or now),
            "updated_at": str(payload.get("updated_at") or now),
        }
        with self.store.connection() as connection:
            connection.execute(
                """
                INSERT INTO strategy_campaigns VALUES (
                    :campaign_id, :strategy_id, :state, :commitment_digest, :artifact_digest,
                    :artifact_byte_length, :public_document_json, :policy_json, :generator_bundle_digest,
                    :secret_seed_material_hex, :instruments_json, :steps, :result_json, :created_at, :updated_at
                )
                ON CONFLICT(campaign_id) DO UPDATE SET
                    state=excluded.state,
                    commitment_digest=excluded.commitment_digest,
                    artifact_digest=excluded.artifact_digest,
                    artifact_byte_length=excluded.artifact_byte_length,
                    public_document_json=excluded.public_document_json,
                    policy_json=excluded.policy_json,
                    generator_bundle_digest=excluded.generator_bundle_digest,
                    secret_seed_material_hex=excluded.secret_seed_material_hex,
                    instruments_json=excluded.instruments_json,
                    steps=excluded.steps,
                    result_json=excluded.result_json,
                    updated_at=excluded.updated_at
                """,
                row,
            )
        value = dict(row)
        value["public_document"] = json.loads(str(value.pop("public_document_json")))
        value["policy"] = json.loads(str(value.pop("policy_json")))
        value["instruments"] = json.loads(str(value.pop("instruments_json")))
        value["result"] = json.loads(str(value.pop("result_json"))) if row["result_json"] else None
        return value

    def campaign(self, campaign_id: str) -> dict[str, Any]:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM strategy_campaigns WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()
        if row is None:
            raise KeyError(campaign_id)
        value = dict(row)
        value["public_document"] = json.loads(str(value.pop("public_document_json")))
        value["policy"] = json.loads(str(value.pop("policy_json")))
        value["instruments"] = json.loads(str(value.pop("instruments_json")))
        value["result"] = json.loads(str(value.pop("result_json"))) if row["result_json"] else None
        return value

    def save_failure(self, payload: dict[str, Any]) -> dict[str, Any]:
        failure_id = str(payload.get("failure_id") or self._new_id("failure"))
        now = self._now()
        row = {
            "failure_id": failure_id,
            "strategy_id": str(payload["strategy_id"]),
            "campaign_id": payload.get("campaign_id"),
            "backtest_id": payload.get("backtest_id"),
            "category": str(payload.get("category") or "unknown"),
            "severity": str(payload.get("severity") or "medium"),
            "evidence_json": json.dumps(payload.get("evidence") or payload, sort_keys=True),
            "minimized_candidate_json": json.dumps(payload.get("minimized_candidate") or {}, sort_keys=True)
            if payload.get("minimized_candidate")
            else None,
            "replay_artifact_id": payload.get("replay_artifact_id"),
            "suggestions_json": json.dumps(payload.get("suggestions") or [], sort_keys=True),
            "extra_json": json.dumps(payload.get("extra") or {}, sort_keys=True),
            "created_at": str(payload.get("created_at") or now),
        }
        if not row["strategy_id"] and row["campaign_id"]:
            try:
                row["strategy_id"] = self.campaign(str(row["campaign_id"]))["strategy_id"]
            except KeyError:
                row["strategy_id"] = ""
        with self.store.connection() as connection:
            connection.execute(
                """
                INSERT INTO strategy_failures VALUES (
                    :failure_id, :strategy_id, :campaign_id, :backtest_id, :category,
                    :severity, :evidence_json, :minimized_candidate_json, :replay_artifact_id,
                    :suggestions_json, :extra_json, :created_at
                )
                """,
                row,
            )
        return {
            **row,
            "evidence": json.loads(str(row["evidence_json"])),
            "suggestions": json.loads(str(row["suggestions_json"])),
        }

    def failures_for_campaign(self, campaign_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM strategy_failures WHERE campaign_id = ? ORDER BY created_at, failure_id",
                (campaign_id,),
            ).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            value["evidence"] = json.loads(str(value.pop("evidence_json")))
            value["minimized_candidate"] = (
                json.loads(str(value.pop("minimized_candidate_json")))
                if row["minimized_candidate_json"]
                else None
            )
            value["suggestions"] = json.loads(str(value.pop("suggestions_json")))
            value["extra"] = json.loads(str(value.pop("extra_json")))
            values.append(value)
        return values

    def save_replay_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload.get("event_id") or self._new_id("event"))
        now = self._now()
        row = {
            "event_id": event_id,
            "failure_id": str(payload["failure_id"]),
            "campaign_id": payload.get("campaign_id"),
            "step_index": int(payload.get("step_index") or 0),
            "event_kind": str(payload.get("event_kind") or "observation"),
            "event_json": json.dumps(payload.get("event") or payload, sort_keys=True, separators=(",", ":")),
            "recorded_at": str(payload.get("recorded_at") or now),
        }
        with self.store.connection() as connection:
            connection.execute(
                "INSERT INTO strategy_replay_events VALUES (:event_id, :failure_id, :campaign_id, :step_index, :event_kind, :event_json, :recorded_at)",
                row,
            )
        return {**row, "event": json.loads(str(row["event_json"]))}

    def replay_events_for_failure(self, failure_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM strategy_replay_events WHERE failure_id = ? ORDER BY step_index, event_id",
                (failure_id,),
            ).fetchall()
        return [{**dict(row), "event": json.loads(str(row["event_json"]))} for row in rows]

    def save_evidence_export(self, payload: dict[str, Any]) -> dict[str, Any]:
        export_id = str(payload.get("export_id") or self._new_id("export"))
        now = self._now()
        row = {
            "export_id": export_id,
            "campaign_id": str(payload["campaign_id"]),
            "scope": str(payload.get("scope") or "full"),
            "manifest_json": json.dumps(payload.get("manifest") or {}, sort_keys=True),
            "report_html": str(payload.get("report_html") or ""),
            "csv_hashes_json": json.dumps(payload.get("csv_hashes") or {}, sort_keys=True),
            "created_by": str(payload.get("created_by") or "system"),
            "created_at": str(payload.get("created_at") or now),
        }
        with self.store.connection() as connection:
            connection.execute(
                """
                INSERT INTO strategy_evidence_exports VALUES (
                    :export_id, :campaign_id, :scope, :manifest_json, :report_html,
                    :csv_hashes_json, :created_by, :created_at
                )
                """,
                row,
            )
        return {
            **row,
            "manifest": json.loads(str(row["manifest_json"])),
            "csv_hashes": json.loads(str(row["csv_hashes_json"])),
        }

    def evidence_export(self, export_id: str) -> dict[str, Any]:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM strategy_evidence_exports WHERE export_id = ?", (export_id,)
            ).fetchone()
        if row is None:
            raise KeyError(export_id)
        value = dict(row)
        value["manifest"] = json.loads(str(value.pop("manifest_json")))
        value["csv_hashes"] = json.loads(str(value.pop("csv_hashes_json")))
        return value
