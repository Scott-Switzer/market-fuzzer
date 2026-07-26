"""Authoritative versioned API: /api/strategy-lab/v2.

Every endpoint routes through the canonical application-service layer
(compilation -> approval -> persistence -> executor -> accounting -> campaign ->
evidence). No legacy planner, DSL strategy model, or legacy approval service is
used on the authoritative path.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, sessionmaker

from app.domain.strategy_spec import StrategySpec
from app.persistence.database import make_engine, make_session_factory
from app.strategy_lab.canonical.approval_service import approve_spec
from app.strategy_lab.canonical.backtest_service import run_backtest
from app.strategy_lab.canonical.campaign_service import run_campaign
from app.strategy_lab.canonical.compilation_service import compile_text, resolve
from app.strategy_lab.canonical.contracts import (
    ApproveRequest,
    ApproveResponse,
    AuditRecord,
    BacktestRequest,
    BacktestResponse,
    CampaignRequest,
    CampaignResponse,
    CompileRequest,
    CompileResponse,
    ProjectCreatedResponse,
    ResolveRequest,
    ResolveResponse,
)
from app.strategy_lab.canonical.errors import (
    BoundedExecutionLimitError,
    CanonicalError,
    DataUnavailableError,
    HashMismatchError,
    PanelTooShortError,
    UnregisteredExecutorError,
    UnresolvedClauseError,
)
from app.strategy_lab.canonical.evidence_service import build_audit_record


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    engine = make_engine()
    return make_session_factory(engine)


def get_session() -> Iterator[Session]:
    factory = _session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


router = APIRouter(prefix="/api/strategy-lab/v2", tags=["strategy-lab-v2"])

# FastAPI dependency alias (avoids B008 call-in-defaults on every endpoint).
DbSession = Annotated[Session, Depends(get_session)]


def _err(status: int, exc: CanonicalError) -> HTTPException:
    return HTTPException(status_code=status, detail=str(exc))


@router.post("/projects", response_model=ProjectCreatedResponse, status_code=201)
def create_project(body: dict, session: DbSession) -> ProjectCreatedResponse:
    from app.persistence.repositories import ProjectRepository

    name = (body or {}).get("name", "Fenrix Workspace")
    proj = ProjectRepository(session).create(name=name, owner="local")
    return ProjectCreatedResponse(project_id=proj.id, name=proj.name, created_at=proj.created_at.isoformat())


@router.post("/compile", response_model=CompileResponse)
def compile_endpoint(body: CompileRequest) -> CompileResponse:
    # The ONLY compiler on the authoritative path.
    return compile_text(body.description)


@router.post("/resolve", response_model=ResolveResponse)
def resolve_endpoint(body: ResolveRequest) -> ResolveResponse:
    try:
        return resolve(body.spec_draft, body.resolutions)
    except UnresolvedClauseError as exc:
        raise _err(422, exc) from exc


@router.post("/approve", response_model=ApproveResponse)
def approve_endpoint(body: ApproveRequest, session: DbSession) -> ApproveResponse:
    spec = StrategySpec.model_validate(body.spec_draft)
    try:
        return approve_spec(
            session,
            project_id=body.project_id,
            spec=spec,
            spec_hash=spec.compute_hash(),
        )
    except (UnresolvedClauseError, UnregisteredExecutorError, HashMismatchError) as exc:
        raise _err(422, exc) from exc
    except CanonicalError as exc:
        raise _err(400, exc) from exc


@router.post("/backtests", response_model=BacktestResponse)
def backtest_endpoint(body: BacktestRequest, session: DbSession) -> BacktestResponse:
    try:
        return run_backtest(
            session,
            strategy_id=body.strategy_id,
            strategy_version=body.strategy_version,
            expected_canonical_hash=body.expected_canonical_hash,
            data_source=body.data_source.model_dump(mode="python"),
            project_id=body.strategy_id,  # project linkage retained via approval's project
            initial_capital=body.initial_capital,
            idempotency_key=body.idempotency_key,
        )
    except HashMismatchError as exc:
        raise _err(422, exc) from exc
    except (DataUnavailableError, PanelTooShortError, BoundedExecutionLimitError) as exc:
        raise _err(422, exc) from exc
    except CanonicalError as exc:
        raise _err(400, exc) from exc


@router.post("/campaigns", response_model=CampaignResponse)
def campaign_endpoint(body: CampaignRequest, session: DbSession) -> CampaignResponse:
    try:
        return run_campaign(
            session,
            strategy_id=body.strategy_id,
            strategy_version=body.strategy_version,
            expected_canonical_hash=body.expected_canonical_hash,
            mechanism_families=body.mechanism_families,
            seed_list=body.seed_list,
            world_budget=body.world_budget,
            failure_predicates=body.failure_predicates,
            project_id=body.strategy_id,
            baseline_run_id=body.baseline_run_id,
            data_source=body.data_source.model_dump(mode="python") if body.data_source else None,
            idempotency_key=body.idempotency_key,
        )
    except HashMismatchError as exc:
        raise _err(422, exc) from exc
    except CanonicalError as exc:
        raise _err(400, exc) from exc


@router.get("/strategies/{strategy_id}/versions/{strategy_version}", response_model=dict)
def get_strategy_version(strategy_id: str, strategy_version: int, session: DbSession) -> dict:
    from app.persistence.repositories import StrategyRepository

    approved = StrategyRepository(session).get_approved_version(strategy_id, strategy_version)
    if approved is None:
        raise HTTPException(404, "approved version not found")
    if not approved.verify():
        raise HTTPException(500, "server integrity error: stored canonical hash mismatch")
    spec = approved.to_spec()
    return {
        "api_version": "v2",
        "strategy_id": approved.strategy_id,
        "strategy_version": approved.version,
        "canonical_hash": approved.canonical_hash,
        "schema_version": approved.schema_version,
        "approved_by": approved.approved_by,
        "approved_at": approved.approved_at.isoformat(),
        "spec": spec.model_dump(mode="python"),
    }


@router.get("/audit", response_model=AuditRecord)
def audit_endpoint(
    strategy_id: str,
    strategy_version: int,
    session: DbSession,
    run_id: str | None = None,
    campaign_id: str | None = None,
    failure_id: str | None = None,
) -> AuditRecord:
    return build_audit_record(
        session,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        run_id=run_id,
        campaign_id=campaign_id,
        failure_id=failure_id,
    )


__all__ = ["router"]
