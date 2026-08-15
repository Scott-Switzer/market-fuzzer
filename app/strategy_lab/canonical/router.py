"""Authoritative versioned API: /api/strategy-lab/v2.

Every endpoint routes through the canonical application-service layer
(compilation -> approval -> persistence -> executor -> accounting -> campaign ->
evidence). Idempotency keys are reserved for approve/backtest/campaign; the
audit record verifies resource relationships and artifact integrity.
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
    ProjectCreateRequest,
    ResolveRequest,
    ResolveResponse,
)
from app.strategy_lab.canonical.durable import (
    cancel_job,
    get_default_store,
    verify_artifacts,
)
from app.strategy_lab.canonical.errors import (
    BaselineMismatchError,
    BoundedExecutionLimitError,
    CanonicalError,
    DataUnavailableError,
    HashMismatchError,
    IdempotencyConflictError,
    IdempotencyInFlightError,
    PanelTooShortError,
    PriorAttemptFailedError,
    ResourceNotFoundError,
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
def create_project(body: ProjectCreateRequest, session: DbSession) -> ProjectCreatedResponse:
    from app.persistence.repositories import ProjectRepository

    proj = ProjectRepository(session).create(name=body.name, owner="local")
    return ProjectCreatedResponse(project_id=proj.id, name=proj.name, created_at=proj.created_at.isoformat())


@router.post("/compile", response_model=CompileResponse)
def compile_endpoint(body: CompileRequest) -> CompileResponse:
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
            actor=body.actor,
            idempotency_key=body.idempotency_key,
        )
    except (UnresolvedClauseError, UnregisteredExecutorError, HashMismatchError) as exc:
        raise _err(422, exc) from exc
    except (IdempotencyConflictError, IdempotencyInFlightError, PriorAttemptFailedError) as exc:
        raise _err(409, exc) from exc
    except CanonicalError as exc:
        raise _err(422, exc) from exc


@router.post("/backtests", response_model=BacktestResponse)
def backtest_endpoint(body: BacktestRequest, session: DbSession) -> BacktestResponse:
    try:
        return run_backtest(
            session,
            strategy_id=body.strategy_id,
            strategy_version=body.strategy_version,
            expected_canonical_hash=body.expected_canonical_hash,
            data_source=body.data_source.model_dump(mode="python"),
            project_id=None,
            initial_capital=body.initial_capital,
            idempotency_key=body.idempotency_key,
        )
    except HashMismatchError as exc:
        raise _err(422, exc) from exc
    except (DataUnavailableError, PanelTooShortError, BoundedExecutionLimitError) as exc:
        raise _err(422, exc) from exc
    except (IdempotencyConflictError, IdempotencyInFlightError, PriorAttemptFailedError) as exc:
        raise _err(409, exc) from exc
    except CanonicalError as exc:
        raise _err(422, exc) from exc


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
            failure_predicates=[p.model_dump(mode="json") for p in body.failure_predicates],
            project_id=None,
            baseline_run_id=body.baseline_run_id,
            data_source=body.data_source.model_dump(mode="python") if body.data_source else None,
            idempotency_key=body.idempotency_key,
        )
    except HashMismatchError as exc:
        raise _err(422, exc) from exc
    except (IdempotencyConflictError, IdempotencyInFlightError, PriorAttemptFailedError) as exc:
        raise _err(409, exc) from exc
    except (BaselineMismatchError, CanonicalError) as exc:
        raise _err(422, exc) from exc


@router.get("/runs/{run_id}", response_model=dict)
def get_run(run_id: str, session: DbSession) -> dict:
    from app.persistence.repositories import RunRepository

    run = RunRepository(session).get(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return {
        "run_id": run.id,
        "project_id": run.project_id,
        "strategy_id": run.strategy_id,
        "strategy_version": run.strategy_version,
        "canonical_hash": run.strategy_hash,
        "data_mode": run.data_mode,
        "status": run.status,
        "limitations": run.limitations,
    }


@router.get("/runs/{run_id}/result", response_model=dict)
def get_run_result(run_id: str, session: DbSession) -> dict:
    return _load_run_result(session, run_id)


def _load_run_result(session: Session, run_id: str) -> dict:
    from app.persistence.repositories import RunRepository

    run = RunRepository(session).get(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    store = get_default_store()
    try:
        verified = verify_artifacts(session, store=store, run_id=run_id)
    except Exception as exc:  # ArtifactIntegrityError or missing
        raise HTTPException(500, f"artifact integrity error: {exc}") from exc
    manifest = _load_json(store, f"runs/{run_id}/result-manifest.json")
    metrics = _load_json(store, f"runs/{run_id}/metrics.json")
    equity = _load_json(store, f"runs/{run_id}/equity-curve.json")
    trades = _load_json(store, f"runs/{run_id}/trades.json")
    prov = _load_json(store, f"runs/{run_id}/data-provenance.json")
    return {
        "run_id": run_id,
        "project_id": run.project_id,
        "strategy_id": run.strategy_id,
        "strategy_version": run.strategy_version,
        "canonical_hash": run.strategy_hash,
        "data_content_digest": (manifest or {}).get("data_content_digest"),
        "manifest": manifest,
        "metrics": metrics,
        "equity_curve": equity,
        "trades": trades,
        "data_provenance": prov,
        "artifact_references": verified,
    }


def _load_json(store, key: str):
    raw = store.get(key)
    if raw is None:
        return None
    import json as _json

    return _json.loads(raw)


@router.get("/campaigns/{campaign_id}", response_model=dict)
def get_campaign(campaign_id: str, session: DbSession) -> dict:
    from sqlalchemy import select

    from app.persistence.models import CampaignRow

    camp = session.scalar(select(CampaignRow).where(CampaignRow.id == campaign_id))
    if camp is None:
        raise HTTPException(404, "campaign not found")
    store = get_default_store()
    manifest = _load_json(store, f"campaigns/{campaign_id}/manifest.json")
    return {
        "campaign_id": camp.id,
        "run_id": camp.run_id,
        "project_id": camp.project_id,
        "strategy_id": camp.strategy_id,
        "strategy_version": camp.strategy_version,
        "canonical_hash": camp.strategy_hash,
        "requested_worlds": camp.requested_worlds,
        "evaluated_worlds": camp.evaluated_worlds,
        "predicate_failures": camp.predicate_failures,
        "evaluation_errors": camp.evaluation_errors,
        "error_rate": camp.error_rate,
        "errors_by_mechanism": camp.errors_by_mechanism,
        "failure_rate_by_mechanism": camp.failure_rate_by_mechanism,
        "manifest": manifest,
    }


@router.post("/backtests/{run_id}/cancel", response_model=dict)
def cancel_backtest(run_id: str, session: DbSession) -> dict:
    """Durably cancel an in-flight backtest. Idempotent: a terminal run returns
    ``cancelled=false``. Restart-safe (operates on the persisted job)."""
    cancelled = cancel_job(session, run_id=run_id)
    session.commit()
    return {"run_id": run_id, "cancelled": cancelled}


@router.post("/campaigns/{campaign_id}/cancel", response_model=dict)
def cancel_campaign(campaign_id: str, session: DbSession) -> dict:
    """Durably cancel an in-flight campaign (resolves its run's job)."""
    from sqlalchemy import select

    from app.persistence.models import CampaignRow

    camp = session.scalar(select(CampaignRow).where(CampaignRow.id == campaign_id))
    if camp is None:
        raise HTTPException(404, "campaign not found")
    cancelled = cancel_job(session, run_id=camp.run_id)
    session.commit()
    return {"campaign_id": campaign_id, "run_id": camp.run_id, "cancelled": cancelled}


@router.get("/failures/{failure_id}", response_model=dict)
def get_failure(failure_id: str, session: DbSession) -> dict:
    from sqlalchemy import select

    from app.persistence.models import WorldEvaluationRow

    ev = session.scalar(select(WorldEvaluationRow).where(WorldEvaluationRow.id == failure_id))
    if ev is None:
        raise HTTPException(404, "failure not found")
    return {
        "failure_id": ev.id,
        "campaign_id": ev.campaign_id,
        "world_id": ev.world_id,
        "outcome": ev.outcome,
        "predicate_results": ev.predicate_results,
        "metrics": ev.metrics,
        "error_message": ev.error_message,
    }


@router.get("/failures/{failure_id}/replay", response_model=dict)
def replay_failure(failure_id: str, session: DbSession) -> dict:
    """Reconstruct a confirmed failure from stored scenario + result artifacts."""
    from sqlalchemy import select

    from app.persistence.models import ScenarioWorldRow, WorldEvaluationRow

    ev = session.scalar(select(WorldEvaluationRow).where(WorldEvaluationRow.id == failure_id))
    if ev is None:
        raise HTTPException(404, "failure not found")
    world = session.get(ScenarioWorldRow, ev.world_id)
    if world is None:
        raise HTTPException(404, "scenario world not found")
    return {
        "failure_id": ev.id,
        "campaign_id": ev.campaign_id,
        "world_id": world.id,
        "mechanism": world.mechanism,
        "seed": world.seed,
        "intensity": world.intensity,
        "definition": world.definition,
        "scenario_content_digest": world.content_digest,
        "diagnostics": world.diagnostics,
        "outcome": ev.outcome,
        "predicate_results": ev.predicate_results,
        "metrics": ev.metrics,
    }


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
    project_id: str | None = None,
    run_id: str | None = None,
    campaign_id: str | None = None,
    failure_id: str | None = None,
) -> AuditRecord:
    try:
        return build_audit_record(
            session,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            project_id=project_id,
            run_id=run_id,
            campaign_id=campaign_id,
            failure_id=failure_id,
        )
    except ResourceNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except CanonicalError as exc:
        raise HTTPException(422, str(exc)) from exc


__all__ = ["router"]
