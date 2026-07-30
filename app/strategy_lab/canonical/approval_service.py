"""Approval service: validated StrategySpec -> immutable ApprovedStrategyVersion
-> durable persistence, returning stable identity/version/hash.

Authoritative approval path. Does NOT use the legacy DSL Strategy model or the
legacy service-layer approval / ledger-hash flow.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.strategy_spec import StrategySpec
from app.domain.strategy_version import DraftStrategy
from app.persistence.models import Project, Strategy, StrategyVersionRow
from app.persistence.repositories import ProjectRepository, StrategyRepository
from app.strategies.registry import default_registry
from app.strategy_lab.canonical.contracts import ApproveResponse
from app.strategy_lab.canonical.errors import (
    HashMismatchError,
    UnregisteredExecutorError,
    UnresolvedClauseError,
)


def approve_spec(
    session: Session,
    *,
    project_id: str,
    spec: StrategySpec,
    spec_hash: str,
    actor: str = "user",
    idempotency_key: str,
) -> ApproveResponse:
    """Validate, approve, persist, reload, and re-verify the approved version.

    Fail-closed gate (steps 2-6 of the cutover brief): recompute the canonical
    hash, reject unresolved/unsupported clauses, confirm a registered executor
    backs the type, reject if required data fields are unsatisfiable.

    Idempotent: a prior approval of the same (strategy_id, version) returns the
    stored version; the unique constraint is the authority.

    Returns 15-step-verified identity/version/hash (reload -> to_spec -> verify).
    """
    # 2-6. Fail-closed gate.
    supported = default_registry.supported_types()
    reasons = spec.blocking_reasons(supported_types=supported)
    if reasons:
        if spec.strategy_type.value == "unsupported":
            raise UnregisteredExecutorError(reasons[0])
        raise UnresolvedClauseError("; ".join(reasons))
    if spec.strategy_type not in supported:
        raise UnregisteredExecutorError(f"strategy type {spec.strategy_type} has no registered executor")

    live_hash = spec.compute_hash()
    if live_hash != spec_hash:
        raise HashMismatchError(f"hash drift at approval: live={live_hash} supplied={spec_hash}")

    # Idempotency control (Phase 2.6 D3 / D11): the IDEMPOTENCY KEY -- not the
    # canonical hash -- controls request replay. Reusing the same key with the
    # same request returns the original resource; reusing it with a DIFFERENT
    # request is a conflict (409). Distinct keys with identical content are
    # allowed to create distinct logical strategies (identity != content).
    from app.strategy_lab.canonical.durable import reserve_idempotency

    repo = StrategyRepository(session)
    request_payload = {
        "project_id": project_id,
        "spec_hash": live_hash,
        "actor": actor,
    }
    existing_ir, created = reserve_idempotency(
        session,
        scope="approve",
        project_id=project_id,
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        resource_type="strategy_version",
        resource_id="",  # filled below
        response_json={},
    )
    if not created:
        # Concurrent or prior reservation: only the creator may execute.
        if existing_ir.resource_id:
            stored0 = repo.get_approved_version(
                existing_ir.resource_id.split(":")[0], int(existing_ir.resource_id.split(":")[1])
            )
            if stored0 is not None:
                return ApproveResponse(
                    api_version="v2",
                    project_id=project_id,
                    strategy_id=stored0.strategy_id,
                    strategy_version=stored0.version,
                    canonical_hash=stored0.canonical_hash,
                    approved_by=stored0.approved_by,
                    approved_at=stored0.approved_at,
                    schema_version=stored0.schema_version,
                )
        from app.strategy_lab.canonical.errors import IdempotencyInFlightError

        raise IdempotencyInFlightError(
            f"idempotency key {idempotency_key!r} is already reserved for an in-flight approve"
        )

    draft = DraftStrategy(spec=spec)
    # 9. Validated approval (model_validate internally; never model_copy(update=)).
    # Records the REQUEST actor -- never a hardcoded value (Phase 2.6 D10).
    approved = draft.approve(actor, supported_types=supported)
    if live_hash != approved.canonical_hash:
        raise HashMismatchError(
            f"hash drift during approval: live={live_hash} approved={approved.canonical_hash}"
        )

    # 10-11. Persist logical strategy row + approved version.
    if session.get(Project, project_id) is None:
        ProjectRepository(session).create(name="Fenrix Workspace", project_id=project_id)
    if session.get(Strategy, approved.strategy_id) is None:
        repo.create(
            project_id=project_id,
            strategy_id=approved.strategy_id,
            name=spec.name or "untitled",
            original_thesis=spec.original_thesis,
        )
    # Idempotent insert; unique(strategy_id, version) is the authority.
    existing_row = session.scalar(
        select(StrategyVersionRow).where(
            StrategyVersionRow.strategy_id == approved.strategy_id,
            StrategyVersionRow.version == approved.version,
        )
    )
    if existing_row is None:
        repo.add_approved_version(approved)

    # Update the idempotency record with the resolved resource id.
    existing_ir.resource_id = f"{approved.strategy_id}:{approved.version}"

    # 13-15. Reload + reconstruct + verify hash continuity.
    stored = repo.get_approved_version(approved.strategy_id, approved.version)
    if stored is None:
        raise HashMismatchError("failed to reload persisted approved version")
    spec2 = stored.to_spec()  # re-verifies tamper
    if spec2.compute_hash() != stored.canonical_hash:
        raise HashMismatchError("stored hash != reconstructed hash after reload")

    return ApproveResponse(
        api_version="v2",
        project_id=project_id,
        strategy_id=stored.strategy_id,
        strategy_version=stored.version,
        canonical_hash=stored.canonical_hash,
        approved_by=stored.approved_by,
        approved_at=stored.approved_at,
        schema_version=stored.schema_version,
    )


__all__ = ["approve_spec"]
