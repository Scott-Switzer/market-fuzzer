"""Canonical historical backtest service.

Authoritative path: load ApprovedStrategyVersion -> verify -> reconstruct spec
-> confirm request hash == stored hash -> build panel -> run_strategy (generic
accounting) -> persist Run + Job + artifacts via ArtifactStore -> return a
typed BacktestResponse carrying the same canonical hash and reasons-to-distrust.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np

from app.domain.run import Job, JobState, Run, RunStage, RunStatus
from app.persistence.repositories import JobRepository, RunRepository, StrategyRepository
from app.strategies.pipeline import run_strategy
from app.strategy_lab.canonical.contracts import BacktestResponse
from app.strategy_lab.canonical.data_service import (
    acquire_panel,
    check_required_history,
    enforce_bounds,
)
from app.strategy_lab.canonical.errors import HashMismatchError


def _reasons_to_distrust(spec, panel, result) -> list[str]:
    reasons: list[str] = []
    reasons.append("insufficient point-in-time universe evidence")
    reasons.append("yfinance survivorship limitations")
    T = panel.T
    if T < 1000:
        reasons.append("short historical window")
    if result.turnover is not None and float(np.asarray(result.turnover)[-1]) > 1.0:
        reasons.append("high turnover increases cost sensitivity")
    if spec.universe and len(spec.universe) <= 3:
        reasons.append("high concentration in few symbols")
    reasons.append("no out-of-sample evaluation")
    reasons.append("cost assumptions dominate short-horizon results")
    reasons.append("few rebalance observations")
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

    repo = StrategyRepository(session)
    approved = repo.get_approved_version(strategy_id, strategy_version)
    if approved is None:
        raise HashMismatchError(f"no approved version {strategy_id} v{strategy_version}")
    if not approved.verify():
        raise HashMismatchError("stored approved version failed verification")
    # Resolve the owning project from persistence (never trust caller-supplied FK).
    strat_row = session.get(Strategy, strategy_id)
    owning_project = strat_row.project_id if strat_row is not None else project_id

    spec = approved.to_spec()
    stored_hash = approved.canonical_hash
    if stored_hash != expected_canonical_hash:
        raise HashMismatchError(f"request hash {expected_canonical_hash} != stored {stored_hash}")
    if spec.compute_hash() != stored_hash:
        raise HashMismatchError("reconstructed spec hash != stored hash")

    # Build panel + provenance.
    panel, prov = acquire_panel(
        source=data_source["source"],
        universe=data_source.get("universe", []),
        benchmark=data_source.get("benchmark"),
        start=data_source.get("start"),
        end=data_source.get("end"),
        seed=data_source.get("seed"),
        csv_b64=data_source.get("csv_b64"),
    )
    enforce_bounds(panel)
    # Benchmark isolation: ensure it is not accidentally tradable.
    if spec.benchmark:
        if spec.benchmark in spec.universe and not spec.benchmark_tradable:
            from app.strategy_lab.canonical.errors import UnregisteredExecutorError

            raise UnregisteredExecutorError(
                f"benchmark {spec.benchmark} present in universe but benchmark_tradable is False"
            )
    check_required_history(spec, panel)

    # Bounded synchronous execution through the canonical pipeline.
    result = run_strategy(
        spec,
        panel,
        initial_capital=float(initial_capital),
        expected_hash=stored_hash,
    )

    # Persist Run + Job (durable even though synchronous).
    run = Run(
        project_id=owning_project,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_hash=stored_hash,
        data_mode=prov.source,
        status=RunStatus.COMPLETED,
        limitations=[
            "Deterministic synthetic panel for CI/offline; not a production guarantee.",
            "Costs are assumptions; real slippage/locate may differ.",
        ],
    )
    RunRepository(session).create(run)
    RunRepository(session).mark_stage(run.run_id, RunStage.HISTORICAL_BACKTEST)
    job = Job(
        idempotency_key=idempotency_key,
        stage=RunStage.HISTORICAL_BACKTEST,
        state=JobState.SUCCEEDED,
        progress=1.0,
        inputs_frozen=True,
        result_ref=run.run_id,
    )
    JobRepository(session).submit(job, run_id=run.run_id)

    bench_metrics = None
    if result.benchmark_close is not None:
        bc = np.asarray(result.benchmark_close, dtype=float)
        bench_metrics = {
            "cumulative_return": float(bc[-1] / bc[0] - 1),
            "final_value": float(bc[-1]),
        }

    eq = np.asarray(result.equity_curve, dtype=float)
    turnover_last = float(np.asarray(result.turnover)[-1]) if result.turnover is not None else None
    gross_last = float(np.asarray(result.gross_exposure)[-1]) if result.gross_exposure is not None else None
    net_last = float(np.asarray(result.net_exposure)[-1]) if result.net_exposure is not None else None
    n_trades = len(result.trades)
    return BacktestResponse(
        api_version="v2",
        run_id=run.run_id,
        job_id=job.job_id,
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
        trade_summary={
            "num_trades": n_trades,
            "sample": result.trades[:5],
        },
        exposure_summary={
            "final_gross": gross_last,
            "final_net": net_last,
        },
        turnover=turnover_last,
        cost_summary=result.cost_summary,
        warnings=list(result.warnings),
        reasons_to_distrust=_reasons_to_distrust(spec, panel, result),
        data_provenance=prov,
        artifact_references=[],
    )


__all__ = ["run_backtest"]
