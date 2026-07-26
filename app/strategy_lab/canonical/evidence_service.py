"""Evidence service: build auditable records and artifact references for the
canonical product path. No exchange-origin claims are made.
"""

from __future__ import annotations

from app.persistence.repositories import RunRepository, StrategyRepository
from app.strategy_lab.canonical.contracts import AuditRecord
from app.strategy_lab.canonical.errors import HashMismatchError


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
        raise HashMismatchError(f"no approved version {strategy_id} v{strategy_version}")
    artifact_hashes: list[str] = []
    data_digest = None
    if run_id is not None:
        run_row = RunRepository(session).get(run_id)
        if run_row is not None:
            data_digest = run_row.data_mode
    return AuditRecord(
        api_version="v2",
        project_id=project_id,
        strategy_id=approved.strategy_id,
        strategy_version=approved.version,
        canonical_hash=approved.canonical_hash,
        run_id=run_id,
        campaign_id=campaign_id,
        failure_id=failure_id,
        data_source_digest=data_digest,
        artifact_hashes=artifact_hashes,
        compiler_version="deterministic/1.0",
        schema_version=approved.schema_version,
        limitations=[
            "Synthetic campaign results are bar-level scenario replays, not order-book event streams.",
            "Historical panel may be deterministic fixture; label accordingly.",
        ],
    )


__all__ = ["build_audit_record"]
