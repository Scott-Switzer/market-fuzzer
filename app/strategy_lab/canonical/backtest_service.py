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
    """Reconstruct the typed response from persisted artifacts (idempotent replay)."""
    from app.persistence.repositories import RunRepository

    run = RunRepository(session).get(run_id)
    if run is None:
        from fastapi import HTTPException

        raise HTTPException(404, "run not found")
    store = get_default_store()
    verify_artifacts(session, store=store, run_id=run_id)
    manifest = _read_json(store, f"runs/{run_id}/result-manifest.json") or {}
    metrics = _read_json(store, f"runs/{run_id}/metrics.json") or {}
    equity = _read_json(store, f"runs/{run_id}/equity-curve.json") or {}
    trades = _read_json(store, f"runs/{run_id}/trades.json") or {}
    prov = _read_json(store, f"runs/{run_id}/data-provenance.json") or {}
    cost = _read_json(store, f"runs/{run_id}/cost-summary.json") or {}
    exposures = _read_json(store, f"runs/{run_id}/exposures.json") or {}
    data_prov = DataSourceProvenance.model_validate(prov) if prov else DataSourceProvenance(
        source="unknown", source_name="unknown", requested_symbols=[], returned_symbols=[],
        benchmark=None, start_date=None, end_date=None, retrieval_timestamp=None,
        adjustment_policy="", calendar_policy="", missing_data_policy="",
        coverage_by_symbol={}, warnings=[], content_digest=None,
    )
    eq_list = equity.get("equity", [0.0, 1.0])
    return BacktestResponse(
        api_version="v2",
        run_id=run.id,
        job_id=run.id,
        strategy_id=run.strategy_id,
        strategy_version=run.strategy_version,
        canonical_hash=run.strategy_hash,
        status="completed",
        metrics=metrics,
        benchmark_metrics=None,
        equity_summary={
            "initial_capital": equity.get("initial_capital", 0.0),
            "final_equity": eq_list[-1],
            "cumulative_return": (eq_list[-1] / eq_list[0] - 1) if eq_list[0] else 0.0,
        },
        trade_summary={"num_trades": len(trades or []), "sample": (trades or [])[:5]},
        exposure_summary={
            "final_gross": exposures.get("gross", [None])[-1],
            "final_net": exposures.get("net", [None])[-1],
        },
        turnover=None,
        cost_summary=cost,
        warnings=[],
        reasons_to_distrust=[],
        data_provenance=data_prov,
        artifact_references=manifest.get("artifacts", []),
    )


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
    project_id: str,
    initial_capital: Decimal = Decimal("1000000"),
    idempotency_key: str,
) -> BacktestResponse:
    from app.persistence.models import Strategy

    # Idempotency: reserve (scope, project, key). Same key + same request -> replay.
    from app.strategy_lab.canonical.durable import reserve_idempotency

    request_payload = {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "expected_canonical_hash": expected_canonical_hash,
        "data_source": data_source,
        "initial_capital": str(initial_capital),
    }
    ir = reserve_idempotency(
        session, scope="backtest", project_id=project_id, idempotency_key=idempotency_key,
        request_payload=request_payload, resource_type="run", resource_id="", response_json={},
    )
    if ir.resource_id:
        return load_backtest_result(session, ir.resource_id)

    repo = StrategyRepository(session)
    approved = repo.get_approved_version(strategy_id, strategy_version)
    if approved is None:
        raise HashMismatchError(f"no approved version {strategy_id} v{strategy_version}")
    if not approved.verify():
        raise HashMismatchError("stored approved version failed verification")
    strat_row = session.get(Strategy, strategy_id)
    owning_project = strat_row.project_id if strat_row is not None else project_id

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
        session, run_id=run.id, idempotency_key=idempotency_key, stage=RunStage.HISTORICAL_BACKTEST.value
    )
    _mark_run_stage(session, RunRepository, run.id, RunStage.HISTORICAL_BACKTEST)
    job.state = JobState.RUNNING.value
    job.progress = 0.2
    session.flush()
    from app.strategy_lab.canonical.durable import set_idempotency_resource

    set_idempotency_resource(
        session, scope="backtest", project_id=project_id, idempotency_key=idempotency_key,
        resource_type="run", resource_id=run.id,
    )

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
        gross_last = float(np.asarray(result.gross_exposure)[-1]) if result.gross_exposure is not None else None
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
        write_artifact(session, store=store, run_id=run.id, key=f"runs/{run.id}/request.json",
                       payload=data_source, artifact_index=artifact_index)
        write_artifact(session, store=store, run_id=run.id, key=f"runs/{run.id}/approved-strategy.json",
                       payload=spec.full_json(), artifact_index=artifact_index)
        write_artifact(session, store=store, run_id=run.id, key=f"runs/{run.id}/data-provenance.json",
                       payload=prov.model_dump(mode="json"), artifact_index=artifact_index)
        write_artifact(session, store=store, run_id=run.id, key=f"runs/{run.id}/metrics.json",
                       payload=result.metrics, artifact_index=artifact_index)
        write_artifact(session, store=store, run_id=run.id, key=f"runs/{run.id}/equity-curve.json",
                       payload={"dates": result.dates, "equity": eq.tolist(), "initial_capital": float(initial_capital)},
                       artifact_index=artifact_index)
        write_artifact(session, store=store, run_id=run.id, key=f"runs/{run.id}/trades.json",
                       payload=result.trades, artifact_index=artifact_index)
        write_artifact(session, store=store, run_id=run.id, key=f"runs/{run.id}/exposures.json",
                       payload={
                           "dates": result.dates,
                           "gross": np.asarray(result.gross_exposure).tolist(),
                           "net": np.asarray(result.net_exposure).tolist(),
                       },
                       artifact_index=artifact_index)
        write_artifact(session, store=store, run_id=run.id, key=f"runs/{run.id}/cost-summary.json",
                       payload=result.cost_summary, artifact_index=artifact_index)

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
        write_artifact(session, store=store, run_id=run.id, key=f"runs/{run.id}/result-manifest.json",
                       payload=manifest, artifact_index=artifact_index)

        _mark_run_stage(session, RunRepository, run.id, RunStage.HISTORICAL_BACKTEST)
        _mark_run_stage(session, RunRepository, run.id, RunStage.CALCULATE_METRICS)
        run.status = RunStatus.COMPLETED.value
        job.state = JobState.SUCCEEDED.value
        job.progress = 1.0
        job.result_ref = run.id
        session.flush()

        return BacktestResponse(
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
    except Exception as exc:  # compensating transaction: persist FAILED run/job
        run.status = RunStatus.FAILED.value
        job.state = JobState.FAILED.value
        job.failure = {
            "code": "backtest_execution_error",
            "message": str(exc),
            "retryable": False,
        }
        job.progress = 1.0
        session.flush()
        raise


def _mark_run_stage(session, RunRepo, run_id: str, stage: RunStage) -> None:
    RunRepo(session).mark_stage(run_id, stage)


__all__ = ["run_backtest"]
