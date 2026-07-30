"""Evidence / audit service (Phase 2.6 integrity closure).

The audit endpoint verifies resource relationships (approved -> run -> campaign
-> failure), loads the ACTUAL data content digest and artifact SHA-256 hashes,
uses the REAL compiler version from the approved record, and raises an integrity
error for mismatches or missing records. No placeholder values.
"""

from __future__ import annotations

from app.persistence.models import CampaignRow
from app.persistence.repositories import RunRepository, StrategyRepository
from app.strategy_lab.canonical.contracts import AuditRecord
from app.strategy_lab.canonical.durable import get_default_store, verify_artifacts
from app.strategy_lab.canonical.errors import (
    ArtifactIntegrityError,
    HashMismatchError,
    ResourceNotFoundError,
)


def build_audit_record(
    session,
    *,
    strategy_id: str,
    strategy_version: int,
    project_id: str | None = None,
    run_id: str | None = None,
    campaign_id: str | None = None,
    failure_id: str | None = None,
) -> AuditRecord:
    repo = StrategyRepository(session)
    approved = repo.get_approved_version(strategy_id, strategy_version)
    if approved is None:
        raise ResourceNotFoundError(f"no approved version {strategy_id} v{strategy_version}")
    if not approved.verify():
        raise HashMismatchError("stored approved version failed verification")

    # Validate a supplied project ALWAYS (independent of run), fail-closed
    # (Phase 2.6.1 gate 12): the approved strategy must belong to the project.
    if project_id is not None:
        from app.persistence.models import Strategy

        strat = session.get(Strategy, strategy_id)
        if strat is None:
            raise ResourceNotFoundError(f"strategy {strategy_id} not found")
        if strat.project_id != project_id:
            raise HashMismatchError("strategy does not belong to the requested project")

    # Compiler version is the ACTUAL compiler version used during compilation
    # (recorded in app.compiler.COMPILER_VERSION), NOT the installed package
    # distribution version (Phase 2.6.1 gate 12).
    from app.compiler import COMPILER_VERSION

    compiler_label = COMPILER_VERSION

    # --- verify parent/child relationships (fail-closed) ---
    run_row = None
    if run_id is not None:
        run_row = RunRepository(session).get(run_id)
        if run_row is None:
            raise ResourceNotFoundError(f"run {run_id} not found")
        if run_row.strategy_id != approved.strategy_id or run_row.strategy_version != approved.version:
            raise HashMismatchError("run does not belong to this approved strategy version")
        if project_id is not None and run_row.project_id != project_id:
            raise HashMismatchError("run does not belong to the requested project")
        # Compare the run's recorded canonical hash with the approved hash.
        if run_row.strategy_hash != approved.canonical_hash:
            raise HashMismatchError("run canonical hash does not match the approved strategy hash")

    campaign_row = None
    if campaign_id is not None:
        campaign_row = session.get(CampaignRow, campaign_id)
        if campaign_row is None:
            raise ResourceNotFoundError(f"campaign {campaign_id} not found")
        if (
            campaign_row.strategy_id != approved.strategy_id
            or campaign_row.strategy_version != approved.version
        ):
            raise HashMismatchError("campaign does not belong to this approved strategy version")
        if campaign_row.strategy_hash != approved.canonical_hash:
            raise HashMismatchError("campaign canonical hash does not match the approved strategy hash")
        if project_id is not None and campaign_row.project_id != project_id:
            raise HashMismatchError("campaign does not belong to the requested project")
        if run_row is not None and campaign_row.run_id != run_row.id:
            raise HashMismatchError("campaign does not reference the requested run")

    if failure_id is not None:
        from sqlalchemy import select

        from app.persistence.models import WorldEvaluationRow

        ev = session.scalar(select(WorldEvaluationRow).where(WorldEvaluationRow.id == failure_id))
        if ev is None:
            raise ResourceNotFoundError(f"failure {failure_id} not found")
        if campaign_row is None or ev.campaign_id != campaign_row.id:
            raise HashMismatchError("failure does not belong to the requested campaign")

    # --- actual data content digest + artifact hashes ---
    data_digest = None
    artifact_hashes: list[str] = []
    audit_run_id = None
    if run_row is not None:
        audit_run_id = run_row.id
    elif campaign_row is not None:
        audit_run_id = campaign_row.run_id
    if audit_run_id is not None:
        store = get_default_store()
        try:
            verified = verify_artifacts(session, store=store, run_id=audit_run_id)
        except ArtifactIntegrityError as exc:
            raise HashMismatchError(f"artifact integrity error: {exc}") from exc
        artifact_hashes = [a["sha256"] for a in verified]
        manifest = store.get(f"runs/{audit_run_id}/result-manifest.json")
        if manifest is not None:
            import json as _json

            data_digest = _json.loads(manifest).get("data_content_digest")
        elif campaign_row is not None:
            data_digest = campaign_row.base_panel_digest

    # Limitations reflect the ACTUAL data mode of the audited resource, not a
    # generic constant (Phase 2.6.1 gate 12).
    limitations: list[str] = []
    if campaign_row is not None:
        limitations.append(
            "Synthetic campaign results are bar-level scenario replays, not order-book event streams."
        )
    if run_row is not None:
        if run_row.data_mode in ("demo_fixture", "deterministic_fixture"):
            limitations.append("Historical panel is a deterministic fixture; not real market data.")
        elif run_row.data_mode == "yfinance":
            limitations.append("Historical panel is yfinance data (research/educational; adjusted).")
        else:
            limitations.append(f"Historical panel data mode: {run_row.data_mode}.")
    if not limitations:
        limitations.append("Approved-strategy audit only; no run or campaign artifacts attached.")

    return AuditRecord(
        api_version="v2",
        project_id=project_id or (run_row.project_id if run_row else None),
        strategy_id=approved.strategy_id,
        strategy_version=approved.version,
        canonical_hash=approved.canonical_hash,
        run_id=run_id,
        campaign_id=campaign_id,
        failure_id=failure_id,
        data_source_digest=data_digest,
        artifact_hashes=artifact_hashes,
        compiler_version=compiler_label,
        schema_version=approved.schema_version,
        limitations=limitations,
    )


__all__ = ["build_audit_record"]
