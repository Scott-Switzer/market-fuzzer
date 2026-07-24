"""Submission API: full Fenrix MVP pipeline (compile -> approve -> backtest ->
sealed stress -> minimize -> evidence).

All responses carry the strategy hash so the invariant can be checked end-to-end.
The historical backtest uses the REAL T x N portfolio engine, not the legacy facade.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.strategy_lab.submission.evidence import build_evidence_package
from app.strategy_lab.submission.orchestrator import (
    build_strategy_dsl,
    lock_strategy,
    run_submission,
)
from app.strategy_lab.submission.strategy import (
    BENCHMARK,
    DEMO_UNIVERSE,
    FIXED_END,
    FIXED_START,
    CrossSectionalSpec,
)

router = APIRouter()


class SubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(default="auto", pattern="^(auto|fenrix|yfinance|synthetic_fixture)$")
    use_cache: bool = True
    fenrix_path: str | None = None
    budget: int = Field(default=24, ge=1, le=96)
    universe: list[str] | None = None
    start: str | None = None
    end: str | None = None
    commission_bps: float | None = None
    spread_bps: float | None = None
    slippage_bps: float | None = None
    borrow_bps: float | None = None


def _spec_from(req: SubmissionRequest) -> CrossSectionalSpec:
    return CrossSectionalSpec(
        universe=list(req.universe or DEMO_UNIVERSE),
        benchmark=BENCHMARK,
        start=req.start or FIXED_START,
        end=req.end or FIXED_END,
        commission_bps=req.commission_bps if req.commission_bps is not None else 5.0,
        spread_bps=req.spread_bps if req.spread_bps is not None else 2.0,
        slippage_bps=req.slippage_bps if req.slippage_bps is not None else 3.0,
        borrow_bps=req.borrow_bps if req.borrow_bps is not None else 50.0,
    )


@router.post("/submission/compile")
def compile_endpoint(body: dict[str, Any]) -> dict[str, Any]:
    spec = _spec_from(SubmissionRequest(**(body or {})))
    strategy = build_strategy_dsl(spec)
    return {
        "ok": True,
        "spec": strategy.model_dump(mode="json"),
        "clause_ledger": [c.__dict__ for c in strategy.clause_ledger],
        "universe": spec.universe,
        "benchmark": spec.benchmark,
        "start": spec.start,
        "end": spec.end,
    }


@router.post("/submission/approve")
def approve_endpoint(body: dict[str, Any]) -> dict[str, Any]:
    spec = _spec_from(SubmissionRequest(**(body or {})))
    strategy = build_strategy_dsl(spec)
    approval = lock_strategy(strategy)
    return {"ok": True, **approval}


@router.post("/submission/backtest")
def backtest_endpoint(body: dict[str, Any]) -> dict[str, Any]:
    req = SubmissionRequest(**(body or {}))
    spec = _spec_from(req)
    from app.strategy_lab.submission.engine import run_portfolio_backtest
    from app.strategy_lab.submission.orchestrator import acquire_panel, build_strategy_dsl, lock_strategy

    strategy = build_strategy_dsl(spec)
    approval = lock_strategy(strategy)
    strategy_hash = approval["strategy_id"]
    acquired = acquire_panel(
        mode=req.mode,
        use_cache=req.use_cache,
        fenrix_path=req.fenrix_path,
        universe=list(spec.universe),
        start=spec.start,
        end=spec.end,
    )
    if acquired["panel"] is None:
        raise HTTPException(400, f"data acquisition failed: {acquired.get('error')}")
    res = run_portfolio_backtest(panel=acquired["panel"], spec=spec, strategy_hash=strategy_hash)
    return {
        "ok": True,
        "strategy_hash": strategy_hash,
        "data_mode": acquired["mode"],
        "tier": acquired["tier"],
        "metrics": res.metrics,
        "equity_curve": [round(float(x), 4) for x in res.equity_curve],
        "gross_exposure": [round(float(x), 4) for x in res.gross_exposure],
        "net_exposure": [round(float(x), 4) for x in res.net_exposure],
        "cost_summary": res.cost_summary,
        "assets": res.assets,
        "dates": res.dates,
        "provenance": res.provenance,
    }


@router.post("/submission/stress")
def stress_endpoint(body: dict[str, Any]) -> dict[str, Any]:
    req = SubmissionRequest(**(body or {}))
    spec = _spec_from(req)
    from app.strategy_lab.submission.orchestrator import build_strategy_dsl, lock_strategy
    from app.strategy_lab.submission.stress_search import run_fast_search

    strategy = build_strategy_dsl(spec)
    approval = lock_strategy(strategy)
    strategy_hash = approval["strategy_id"]
    search = run_fast_search(
        strategy_hash=strategy_hash,
        spec=spec,
        base_assets=list(spec.universe)
        if req.mode == "synthetic_fixture"
        else ["SYN_A", "SYN_B", "SYN_C", "SYN_D", "SYN_E", "SYN_F", "SPY"],
        T=504,
        budget=req.budget,
    )
    return {"ok": True, "strategy_hash": strategy_hash, **search}


@router.post("/submission/run")
def run_endpoint(body: dict[str, Any]) -> dict[str, Any]:
    req = SubmissionRequest(**(body or {}))
    try:
        run = run_submission(
            spec=_spec_from(req),
            mode=req.mode,
            use_cache=req.use_cache,
            fenrix_path=req.fenrix_path,
            budget=req.budget,
        )
    except Exception as exc:
        raise HTTPException(500, f"submission run failed: {exc}") from exc
    ev = build_evidence_package(run)
    return {
        "ok": True,
        "strategy_hash": run.strategy_hash,
        "approval": run.approval,
        "data_mode": run.data_mode,
        "backtest": {
            "metrics": run.backtest["metrics"],
            "cost_summary": run.backtest["cost_summary"],
            "equity_curve": run.backtest["equity_curve"],
            "trades": len(run.backtest["trades"]),
        },
        "stress": {
            "evaluated": run.stress["evaluated"],
            "failure_count": run.stress["failure_count"],
            "failure_rate": run.stress["failure_rate"],
            "failed_mechanisms": sorted({f["mechanism"] for f in run.stress["failures"]}),
        },
        "minimized": run.minimized,
        "adjacent_pass": run.adjacent_pass,
        "evidence": {"base_dir": ev["base_dir"], "manifest": ev["manifest"]},
    }
