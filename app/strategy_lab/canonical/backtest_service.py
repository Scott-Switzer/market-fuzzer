"""Canonical historical backtest service (Phase 2.6 durable-results closure).

Persists a complete backtest result to the ArtifactStore (request, approved
strategy, data provenance, metrics, equity curve, trades, exposures, cost
summary, result manifest), indexes every artifact in ``ArtifactIndexRow``, and
returns a populated ``artifact_references``. Run/job follow the durable lifecycle
(PENDING -> RUNNING -> COMPLETED/SUCCEEDED); a failure persists a FAILED run/job
in its own compensating transaction.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import numpy as np

from app.domain.run import JobState, RunStage, RunStatus
from app.persistence.repositories import RunRepository, StrategyRepository
from app.strategies.pipeline import run_strategy
from app.strategy_lab.canonical.contracts import BacktestResponse, DataSourceProvenance
from app.strategy_lab.canonical.data_service import (
    acquire_panel,
    check_required_history,
    enforce_bounds,
)
from app.strategy_lab.canonical.durable import (
    create_pending_run,
    create_queued_job,
    get_default_store,
    verify_artifacts,
    write_artifact,
)
from app.strategy_lab.canonical.errors import HashMismatchError


def load_backtest_result(session, run_id: str) -> BacktestResponse:
    """Return the EXACT original response persisted at completion (replay).

    Verifies artifact integrity, then loads ``runs/{id}/response.json`` — the
    verbatim response stored when the run completed — so a replay is
    semantically identical to the original (Phase 2.6.1 gate 6). A run without
    a persisted response is a failed/incomplete run and is rejected.
    """
    from app.persistence.repositories import RunRepository

    run = RunRepository(session).get(run_id)
    if run is None:
        from fastapi import HTTPException

        raise HTTPException(404, "run not found")
    store = get_default_store()
    verify_artifacts(session, store=store, run_id=run_id)
    stored = _read_json(store, f"runs/{run_id}/response.json")
    if stored is None:
        from app.strategy_lab.canonical.errors import ArtifactIntegrityError

        raise ArtifactIntegrityError(
            f"run {run_id} has no persisted response (status={run.status}); cannot replay"
        )
    return BacktestResponse.model_validate(stored)


def _read_json(store, key):
    raw = store.get(key)
    if raw is None:
        return None
    import json as _json

    return _json.loads(raw)


def _reasons_to_distrust(spec, panel, result) -> list[str]:
    """Conditional reasons-to-distrust from MEASURED facts only (Phase 2.6 D16)."""
    reasons: list[str] = []
    if spec.universe and len(spec.universe) <= 3:
        reasons.append("high concentration in few symbols")
    T = panel.T
    if T < 1000:
        reasons.append("short historical window")
    if result.turnover is not None and float(np.asarray(result.turnover)[-1]) > 1.0:
        reasons.append("high turnover increases cost sensitivity")
    if result.cost_summary is not None:
        total_cost = float(result.cost_summary.get("total", 0.0))
        final_equity = float(np.asarray(result.equity_curve)[-1])
        if final_equity > 0 and abs(total_cost) / final_equity > 0.10:
            reasons.append("costs materially affect the realized return")
    reasons.append("no out-of-sample evaluation")
    if spec.benchmark is None or spec.benchmark_tradable is False:
        reasons.append("synthetic/demo data limitation applies if demo fixture used")
    return reasons


def run_backtest(
    session,
    *,
    strategy_id: str,
    strategy_version: int,
    expected_canonical_hash: str,
    data_source: dict,
    project_id: str | None = None,
    initial_capital: Decimal = Decimal("1000000"),
    idempotency_key: str,
) -> BacktestResponse:
    from app.persistence.models import Strategy

    # Resolve the ACTUAL owning project first (Phase 2.6.1 gate 5): idempotency
    # is scoped to the project, never to the strategy id.
    strat_row = session.get(Strategy, strategy_id)
    if strat_row is None:
        raise HashMismatchError(f"unknown strategy {strategy_id}")
    owning_project = strat_row.project_id
    if project_id is not None and project_id != owning_project:
        raise HashMismatchError("strategy does not belong to the requested project")

    # Idempotency: reserve (scope, project, key). Same key + same request -> replay.
    from app.strategy_lab.canonical.durable import reserve_idempotency

    request_payload = {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "expected_canonical_hash": expected_canonical_hash,
        "data_source": data_source,
        "initial_capital": str(initial_capital),
    }
    ir, created = reserve_idempotency(
        session,
        scope="backtest",
        project_id=owning_project,
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        resource_type="run",
        resource_id="",
        response_json={},
    )
    if not created:
        return _replay_or_reject_backtest(session, ir, idempotency_key)

    repo = StrategyRepository(session)
    approved = repo.get_approved_version(strategy_id, strategy_version)
    if approved is None:
        raise HashMismatchError(f"no approved version {strategy_id} v{strategy_version}")
    if not approved.verify():
        raise HashMismatchError("stored approved version failed verification")

    spec = approved.to_spec()
    stored_hash = approved.canonical_hash
    if stored_hash != expected_canonical_hash:
        raise HashMismatchError(f"request hash {expected_canonical_hash} != stored {stored_hash}")
    if spec.compute_hash() != stored_hash:
        raise HashMismatchError("reconstructed spec hash != stored hash")

    panel, prov = acquire_panel(
        source=data_source["source"],
        universe=data_source.get("universe", []),
        benchmark=data_source.get("benchmark"),
        start=data_source.get("start"),
        end=data_source.get("end"),
        seed=data_source.get("seed"),
        benchmark_tradable=spec.benchmark_tradable,
        csv_b64=data_source.get("csv_b64"),
    )
    enforce_bounds(panel)
    # Benchmark isolation (Phase 2.6 section 6.1): a benchmark requested in the
    # data source that is ALSO a tradable universe member must be explicitly
    # tradable; otherwise the request is rejected (never silently merged).
    ds_bench = data_source.get("benchmark")
    ds_bench_tradable = bool(spec.benchmark_tradable)
    if ds_bench:
        ds_bench_u = ds_bench.upper()
        if ds_bench_u in [u.upper() for u in data_source.get("universe", [])] and not ds_bench_tradable:
            from app.strategy_lab.canonical.errors import BenchmarkConflictError

            raise BenchmarkConflictError(
                f"benchmark {ds_bench} is in the tradable universe but benchmark_tradable is False"
            )
    check_required_history(spec, panel)

    # --- durable lifecycle (Phase 2.6 section 4) ---
    run = create_pending_run(
        session,
        project_id=owning_project,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_hash=stored_hash,
        data_mode=prov.source,
        limitations=[
            "Deterministic synthetic panel for CI/offline; not a production guarantee.",
            "Costs are assumptions; real slippage/locate may differ.",
        ],
    )
    job = create_queued_job(
        session,
        run_id=run.id,
        idempotency_key=idempotency_key,
        stage=RunStage.HISTORICAL_BACKTEST.value,
        scope="backtest",
        project_id=owning_project,
    )
    _mark_run_stage(session, RunRepository, run.id, RunStage.HISTORICAL_BACKTEST)
    job.state = JobState.RUNNING.value
    job.progress = 0.2
    session.flush()
    from app.strategy_lab.canonical.durable import set_idempotency_resource

    set_idempotency_resource(
        session,
        scope="backtest",
        project_id=owning_project,
        idempotency_key=idempotency_key,
        resource_type="run",
        resource_id=run.id,
    )
    # Durability checkpoint (Phase 2.6.1 gate 2): the reservation + pending
    # run/job MUST survive an execution crash, so commit BEFORE executing.
    session.commit()

    store = get_default_store()
    artifact_index: list[dict[str, Any]] = []

    try:
        result = run_strategy(
            spec,
            panel,
            initial_capital=float(initial_capital),
            expected_hash=stored_hash,
        )

        eq = np.asarray(result.equity_curve, dtype=float)
        turnover_last = float(np.asarray(result.turnover)[-1]) if result.turnover is not None else None
        gross_last = (
            float(np.asarray(result.gross_exposure)[-1]) if result.gross_exposure is not None else None
        )
        net_last = float(np.asarray(result.net_exposure)[-1]) if result.net_exposure is not None else None
        n_trades = len(result.trades)

        bench_metrics = None
        if result.benchmark_close is not None:
            bc = np.asarray(result.benchmark_close, dtype=float)
            bench_metrics = {
                "cumulative_return": float(bc[-1] / bc[0] - 1),
                "final_value": float(bc[-1]),
            }

        # --- persist complete artifacts (atomic before success) ---
        write_artifact(
            session,
            store=store,
            run_id=run.id,
            key=f"runs/{run.id}/request.json",
            payload=data_source,
            artifact_index=artifact_index,
        )
        # Exact input panel (Phase 2.6.1 gate 7): campaigns replay against THIS
        # persisted panel, not a re-acquired one.
        from app.strategy_lab.canonical.data_service import panel_to_dict

        write_artifact(
            session,
            store=store,
            run_id=run.id,
            key=f"runs/{run.id}/input-panel.json",
            payload=panel_to_dict(panel),
            artifact_index=artifact_index,
        )
        write_artifact(
            session,
            store=store,
            run_id=run.id,
            key=f"runs/{run.id}/approved-strategy.json",
            payload=spec.full_json(),
            artifact_index=artifact_index,
        )
        write_artifact(
            session,
            store=store,
            run_id=run.id,
            key=f"runs/{run.id}/data-provenance.json",
            payload=prov.model_dump(mode="json"),
            artifact_index=artifact_index,
        )
        write_artifact(
            session,
            store=store,
            run_id=run.id,
            key=f"runs/{run.id}/metrics.json",
            payload=result.metrics,
            artifact_index=artifact_index,
        )
        write_artifact(
            session,
            store=store,
            run_id=run.id,
            key=f"runs/{run.id}/equity-curve.json",
            payload={"dates": result.dates, "equity": eq.tolist(), "initial_capital": float(initial_capital)},
            artifact_index=artifact_index,
        )
        write_artifact(
            session,
            store=store,
            run_id=run.id,
            key=f"runs/{run.id}/trades.json",
            payload=result.trades,
            artifact_index=artifact_index,
        )
        write_artifact(
            session,
            store=store,
            run_id=run.id,
            key=f"runs/{run.id}/exposures.json",
            payload={
                "dates": result.dates,
                "gross": np.asarray(result.gross_exposure).tolist(),
                "net": np.asarray(result.net_exposure).tolist(),
            },
            artifact_index=artifact_index,
        )
        write_artifact(
            session,
            store=store,
            run_id=run.id,
            key=f"runs/{run.id}/cost-summary.json",
            payload=result.cost_summary,
            artifact_index=artifact_index,
        )

        manifest = {
            "schema_version": "result-manifest/v1",
            "project_id": owning_project,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "canonical_hash": stored_hash,
            "run_id": run.id,
            "input_capital": float(initial_capital),
            "data_content_digest": prov.content_digest,
            "executor_type": spec.strategy_type.value,
            "accounting_engine": "generic_accounting/v1",
            "declared_limitations": [
                "Deterministic synthetic panel for CI/offline; not a production guarantee.",
                "Costs are assumptions; real slippage/locate may differ.",
            ],
            "artifacts": artifact_index,
        }
        write_artifact(
            session,
            store=store,
            run_id=run.id,
            key=f"runs/{run.id}/result-manifest.json",
            payload=manifest,
            artifact_index=artifact_index,
        )

        _mark_run_stage(session, RunRepository, run.id, RunStage.HISTORICAL_BACKTEST)
        _mark_run_stage(session, RunRepository, run.id, RunStage.CALCULATE_METRICS)
        run.status = RunStatus.COMPLETED.value
        job.state = JobState.SUCCEEDED.value
        job.progress = 1.0
        job.result_ref = run.id
        session.flush()

        response = BacktestResponse(
            api_version="v2",
            run_id=run.id,
            job_id=job.id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            canonical_hash=stored_hash,
            status="completed",
            metrics=result.metrics,
            benchmark_metrics=bench_metrics,
            equity_summary={
                "initial_capital": float(initial_capital),
                "final_equity": float(eq[-1]),
                "cumulative_return": float(eq[-1] / eq[0] - 1),
            },
            trade_summary={"num_trades": n_trades, "sample": result.trades[:5]},
            exposure_summary={"final_gross": gross_last, "final_net": net_last},
            turnover=turnover_last,
            cost_summary=result.cost_summary,
            warnings=list(result.warnings),
            reasons_to_distrust=_reasons_to_distrust(spec, panel, result),
            data_provenance=DataSourceProvenance.model_validate(prov.model_dump(mode="json")),
            artifact_references=artifact_index,
        )
        # Persist the EXACT response for identical replay (gate 6): both on the
        # idempotency record (fast path) and as a durable artifact (restart-safe).
        ir.response_json = response.model_dump(mode="json")
        write_artifact(
            session,
            store=store,
            run_id=run.id,
            key=f"runs/{run.id}/response.json",
            payload=ir.response_json,
            artifact_index=[],  # not part of the response's own reference list
        )
        session.flush()
        return response
    except Exception as exc:
        # Persist the FAILED lifecycle in a SEPARATE transaction so the
        # request-scoped rollback cannot erase it (Phase 2.6.1 gate 3).
        session.rollback()
        _persist_failed_lifecycle(session, run.id, job.id, exc)
        raise


def _persist_failed_lifecycle(session, run_id: str, job_id: str, exc: Exception) -> None:
    """Persist FAILED run/job in a SEPARATE transaction (survives the request
    rollback). The pending rows were committed before execution began."""
    from app.persistence.models import JobRow, RunRow

    try:
        run = session.get(RunRow, run_id)
        job = session.get(JobRow, job_id)
        if run is not None:
            run.status = RunStatus.FAILED.value
        if job is not None:
            job.state = JobState.FAILED.value
            job.failure_json = {
                "code": "backtest_execution_error",
                "message": str(exc),
                "retryable": False,
            }
            job.progress = 1.0
        session.commit()
    except Exception:  # pragma: no cover - best-effort failure persistence
        session.rollback()


def _replay_or_reject_backtest(session, ir, idempotency_key: str) -> BacktestResponse:
    """Handle a non-creating reservation: complete replay, failed prior, or in-flight."""
    from app.persistence.models import RunRow
    from app.strategy_lab.canonical.errors import IdempotencyInFlightError, PriorAttemptFailedError

    if ir.response_json:
        if ir.resource_id:
            store = get_default_store()
            verify_artifacts(session, store=store, run_id=ir.resource_id)
        return BacktestResponse.model_validate(ir.response_json)
    if ir.resource_id:
        run = session.get(RunRow, ir.resource_id)
        if run is not None and run.status == RunStatus.FAILED.value:
            raise PriorAttemptFailedError(
                f"idempotency key {idempotency_key!r} previously failed; use a new key to retry"
            )
        if run is not None and run.status == RunStatus.COMPLETED.value:
            store = get_default_store()
            verify_artifacts(session, store=store, run_id=ir.resource_id)
            return load_backtest_result(session, ir.resource_id)
        raise IdempotencyInFlightError(
            f"idempotency key {idempotency_key!r} is already reserved for an in-flight backtest"
        )
    raise IdempotencyInFlightError(
        f"idempotency key {idempotency_key!r} is already reserved for an in-flight backtest"
    )


def _mark_run_stage(session, RunRepo, run_id: str, stage: RunStage) -> None:
    RunRepo(session).mark_stage(run_id, stage)


__all__ = ["run_backtest", "load_backtest_result"]
