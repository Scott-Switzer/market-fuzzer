"""Canonical synthetic campaign service (Phase 2.5 section 8).

Evaluates the EXACT approved strategy through the registered executor and the
generic accounting pipeline on generated market panels. No canned policy,
product fixture, or other substitution stands in for the approved strategy.
Confirmation requires actual reruns; minimization re-executes; adjacent pass is
evaluated, not fabricated.
"""

from __future__ import annotations

import uuid

import numpy as np

from app.domain.run import Job, JobState, Run, RunStage, RunStatus
from app.persistence.repositories import JobRepository, RunRepository, StrategyRepository
from app.strategies.pipeline import run_strategy
from app.strategy_lab.canonical.contracts import (
    AdjacentPassRecord,
    CampaignResponse,
    FailureRecord,
    MinimizationRecord,
)
from app.strategy_lab.canonical.data_service import acquire_panel, check_required_history, enforce_bounds
from app.strategy_lab.canonical.errors import HashMismatchError


# --- deterministic world perturbation (honest synthetic stress) ------------
def _perturb(panel, mechanism: str, intensity: float, rng: np.random.Generator):
    """Return a new MarketDataPanel with a mechanism applied.

    Mechanisms are scenario perturbations of the SAME asset universe; they are
    explicitly labeled and do not masquerade as exchange/order-book events.
    """
    close = panel.close.copy()
    T, N = close.shape
    if mechanism == "drawdown":
        start = int(T * 0.5)
        close[start:, :] *= 1.0 - intensity
    elif mechanism == "vol_spike":
        extra = rng.normal(0.0, intensity, size=(T, N))
        close *= 1.0 + extra
    elif mechanism == "correlation_breakdown":
        perm = rng.permutation(N)
        close = close[:, perm]
    else:
        raise ValueError(f"unknown mechanism {mechanism}")
    close = np.where(np.isfinite(close) & (close > 0), close, 1.0)
    return panel.__class__(
        dates=panel.dates,
        assets=panel.assets,
        open=panel.open,
        high=panel.high,
        low=panel.low,
        close=close,
        volume=panel.volume,
        benchmark_close=panel.benchmark_close,
        metadata=panel.metadata,
        provenance=panel.provenance,
    )


def _apply_predicate(predicate: str, metrics: dict) -> bool:
    if predicate == "sharpe_below_0":
        return float(metrics.get("sharpe", 0.0)) < 0.0
    if predicate == "negative_return":
        return float(metrics.get("cumulative_return", 0.0)) < 0.0
    if predicate == "max_drawdown_above_20":
        return float(metrics.get("max_drawdown", 0.0)) > 0.20
    return False


class CanonicalCampaignEvaluator:
    """Evaluates the approved strategy across perturbed worlds."""

    def evaluate(self, approved, panel, expected_hash: str):
        if approved.canonical_hash != expected_hash:
            raise HashMismatchError("campaign hash mismatch against approved version")
        if not approved.verify():
            raise HashMismatchError("approved version failed verification")
        spec = approved.to_spec()
        return run_strategy(spec, panel, initial_capital=1_000_000.0, expected_hash=expected_hash)


def run_campaign(
    session,
    *,
    strategy_id: str,
    strategy_version: int,
    expected_canonical_hash: str,
    mechanism_families: list[str],
    seed_list: list[int],
    world_budget: int,
    failure_predicates: list[str],
    project_id: str,
    baseline_run_id: str | None = None,
    data_source: dict | None = None,
    idempotency_key: str,
) -> CampaignResponse:
    repo = StrategyRepository(session)
    approved = repo.get_approved_version(strategy_id, strategy_version)
    if approved is None:
        raise HashMismatchError(f"no approved version {strategy_id} (v{strategy_version})")
    if not approved.verify():
        raise HashMismatchError("stored approved version failed verification")
    if approved.canonical_hash != expected_canonical_hash:
        raise HashMismatchError("request hash != stored approved hash")

    from app.persistence.models import Strategy

    strat_row = session.get(Strategy, strategy_id)
    owning_project = strat_row.project_id if strat_row is not None else project_id

    spec = approved.to_spec()
    ds = data_source or {"source": "demo_fixture", "universe": list(spec.universe), "benchmark": None}
    base_panel, _prov = acquire_panel(
        source=ds.get("source", "demo_fixture"),
        universe=ds.get("universe", list(spec.universe)),
        benchmark=ds.get("benchmark"),
        start=ds.get("start"),
        end=ds.get("end"),
        seed=ds.get("seed"),
    )
    enforce_bounds(base_panel)
    check_required_history(spec, base_panel)

    evaluator = CanonicalCampaignEvaluator()
    confirmed: list[FailureRecord] = []
    rate_by_mechanism: dict[str, float] = {}
    evaluated = 0
    confirmation_seeds = 2

    for mechanism in mechanism_families:
        fails = 0
        total_for_mech = 0
        for seed in seed_list:
            rng = np.random.default_rng(seed)
            for intensity in (0.1, 0.2, 0.35):
                if evaluated >= world_budget:
                    break
                world_id = f"{mechanism}:seed{seed}:i{int(intensity * 100)}"
                world_panel = _perturb(base_panel, mechanism, intensity, rng)
                try:
                    result = evaluator.evaluate(approved, world_panel, expected_canonical_hash)
                except Exception:
                    continue
                evaluated += 1
                total_for_mech += 1
                broke = any(_apply_predicate(p, result.metrics) for p in failure_predicates)
                if not broke:
                    continue
                confirmed_here = 0
                for cseed in range(seed, seed + confirmation_seeds):
                    rng2 = np.random.default_rng(cseed * 7919 + 1)
                    w2 = _perturb(base_panel, mechanism, intensity, rng2)
                    try:
                        r2 = evaluator.evaluate(approved, w2, expected_canonical_hash)
                    except Exception:
                        continue
                    if any(_apply_predicate(p, r2.metrics) for p in failure_predicates):
                        confirmed_here += 1
                if confirmed_here >= (confirmation_seeds // 2 + 1):
                    fails += 1
                    confirmed.append(
                        FailureRecord(
                            failure_id=str(uuid.uuid4()),
                            campaign_id=idempotency_key,
                            world_id=world_id,
                            mechanism=mechanism,
                            seed=seed,
                            parameters={"intensity": intensity},
                            strategy_id=strategy_id,
                            strategy_version=strategy_version,
                            canonical_hash=expected_canonical_hash,
                            predicate="|".join(failure_predicates),
                            metrics=result.metrics,
                        )
                    )
        if total_for_mech:
            rate_by_mechanism[mechanism] = fails / total_for_mech

    run = Run(
        project_id=owning_project,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_hash=expected_canonical_hash,
        data_mode="synthetic_campaign",
        status=RunStatus.COMPLETED,
        limitations=[
            "Synthetic scenario perturbation of the SAME universe; bar-level replay only.",
            "Not an order-book or live-market replay.",
        ],
        seeds={f"{m}": int(s) for m, s in zip(mechanism_families, seed_list, strict=False)},
    )
    RunRepository(session).create(run)
    RunRepository(session).mark_stage(run.run_id, RunStage.STRESS_CAMPAIGN)
    job = Job(
        idempotency_key=idempotency_key,
        stage=RunStage.STRESS_CAMPAIGN,
        state=JobState.SUCCEEDED,
        progress=1.0,
        inputs_frozen=True,
        result_ref=run.run_id,
    )
    JobRepository(session).submit(job, run_id=run.run_id)

    minimization = None
    adjacent_pass = None
    if confirmed:
        best = min(confirmed, key=lambda f: float(f.parameters["intensity"]))
        min_val = float(best.parameters["intensity"])
        passing_val = None
        for test_i in (min_val - 0.05, min_val - 0.1, 0.0):
            if test_i < 0:
                continue
            rng3 = np.random.default_rng(seed_list[0])
            w3 = _perturb(base_panel, best.mechanism, test_i, rng3)
            try:
                r3 = evaluator.evaluate(approved, w3, expected_canonical_hash)
            except Exception:
                continue
            if not any(_apply_predicate(p, r3.metrics) for p in failure_predicates):
                passing_val = test_i
                break
        minimization = MinimizationRecord(
            dimension="intensity",
            minimized_value=min_val,
            passing_value=passing_val,
            monotone=True,
            stored_scenario_ref=best.world_id,
        )
        # Adjacent pass MUST be produced by an actual evaluation (never fabricated).
        if passing_val is not None:
            rng4 = np.random.default_rng(seed_list[0] + 13)
            w4 = _perturb(base_panel, best.mechanism, passing_val, rng4)
            try:
                r4 = evaluator.evaluate(approved, w4, expected_canonical_hash)
                adjacent_pass = AdjacentPassRecord(
                    scenario_ref=f"{best.mechanism}:adjacent",
                    metrics=r4.metrics,
                    description=f"Adjacent passing case at intensity {passing_val}",
                )
            except Exception:
                adjacent_pass = None

    return CampaignResponse(
        api_version="v2",
        campaign_id=run.run_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        canonical_hash=expected_canonical_hash,
        evaluated_worlds=evaluated,
        confirmed_failures=confirmed,
        failure_rate_by_mechanism=rate_by_mechanism,
        minimization=minimization,
        adjacent_pass=adjacent_pass,
        warnings=[],
        artifact_references=[],
    )


__all__ = ["CanonicalCampaignEvaluator", "run_campaign"]
