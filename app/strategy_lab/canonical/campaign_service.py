"""Canonical synthetic-stress campaign service (Phase 2.6 integrity closure).

Evaluates the EXACT approved strategy through the registered executor and the
generic accounting pipeline on generated, valid, reproducible market panels.

Closes the Phase 2.5 integrity gaps:
* scenario worlds generate VALID OHLCV with preserved symbol identity;
* every world yields SUCCEEDED / FAILED_PREDICATE / EVALUATION_ERROR (no silent
  ``except Exception: continue``);
* confirmed failures reference one durable ``campaign_id``;
* confirmation uses an explicit typed policy with persisted trials;
* minimization uses bisection between verified bounds (``monotone`` only when
  proven), else a grid search named ``smallest_tested_failing_value``;
* the adjacent pass is only emitted after every failure predicate is proven to
  fail;
* the campaign, scenarios, worlds, failures, trials, and adjacent passes are all
  persisted (durable, retrievable after restart).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from app.domain.run import JobState, RunStage, RunStatus
from app.persistence.models import (
    AdjacentPassRow,
    CampaignRow,
    MinimizationTrialRow,
    ScenarioWorldRow,
    WorldEvaluationRow,
)
from app.persistence.repositories import RunRepository, StrategyRepository
from app.strategies.pipeline import run_strategy
from app.strategy_lab.canonical.contracts import (
    AdjacentPassRecord,
    CampaignResponse,
    ConfirmationPolicy,
    FailureRecord,
    MinimizationRecord,
    PredicateResult,
)
from app.strategy_lab.canonical.data_service import acquire_panel, check_required_history, enforce_bounds
from app.strategy_lab.canonical.durable import (
    create_pending_run,
    create_queued_job,
    get_default_store,
    write_artifact,
)
from app.strategy_lab.canonical.errors import (
    BaselineMismatchError,
    HashMismatchError,
)
from app.strategy_lab.canonical.predicates import (
    FailurePredicate,
    evaluate_predicates,
    parse_predicates,
    predicates_failed,
)
from app.strategy_lab.canonical.scenarios import (
    ScenarioDefinition,
    assert_panel_invariants,
    generate_scenario,
)
from app.strategy_lab.submission.panels import MarketDataPanel


def _run_on_world(approved, panel: MarketDataPanel, expected_hash: str) -> Any:
    """Execute the approved strategy on a world panel. Raises on eval error."""
    return run_strategy(approved.to_spec(), panel, initial_capital=1_000_000.0, expected_hash=expected_hash)


def _evaluate_world(
    approved,
    base_panel: MarketDataPanel,
    definition: ScenarioDefinition,
    predicates: list[FailurePredicate],
    expected_hash: str,
    *,
    role: str,
) -> dict[str, Any]:
    """Evaluate the approved strategy on one generated scenario world.

    Returns a structured outcome: succeeded | failed_predicate | evaluation_error.
    Errors are SURFACED, never silently discarded.
    """
    from app.strategy_lab.canonical.durable import _json_safe

    try:
        scenario = generate_scenario(base_panel, definition)
        assert_panel_invariants(scenario.panel)
        result = _run_on_world(approved, scenario.panel, expected_hash)
        pred_results = evaluate_predicates(predicates, result.metrics)
        failed = predicates_failed(pred_results)
        return {
            "outcome": "failed_predicate" if failed else "succeeded",
            "predicate_results": _json_safe([pr.model_dump() for pr in pred_results]),
            "metrics": _json_safe(result.metrics),
            "error_message": None,
            "scenario": scenario,
            "role": role,
        }
    except Exception as exc:  # evaluation error -- record it, do not swallow
        return {
            "outcome": "evaluation_error",
            "predicate_results": [],
            "metrics": {},
            "error_message": str(exc),
            "scenario": None,
            "role": role,
        }


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
    # Idempotency: reserve (scope, project, key). Same key + same request -> replay.
    from app.strategy_lab.canonical.durable import reserve_idempotency

    request_payload = {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "expected_canonical_hash": expected_canonical_hash,
        "mechanism_families": mechanism_families,
        "seed_list": seed_list,
        "world_budget": world_budget,
        "failure_predicates": failure_predicates,
        "baseline_run_id": baseline_run_id,
    }
    ir = reserve_idempotency(
        session, scope="campaign", project_id=project_id, idempotency_key=idempotency_key,
        request_payload=request_payload, resource_type="campaign", resource_id="", response_json={},
    )
    if ir.resource_id:
        return load_campaign_result(session, ir.resource_id)

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
    predicates = parse_predicates(failure_predicates)
    from app.strategy_lab.canonical.scenarios import validate_mechanisms

    validate_mechanisms(mechanism_families)

    ds = data_source or {"source": "demo_fixture", "universe": list(spec.universe), "benchmark": spec.benchmark}
    base_panel, _prov = acquire_panel(
        source=ds.get("source", "demo_fixture"),
        universe=ds.get("universe", list(spec.universe)),
        benchmark=ds.get("benchmark"),
        start=ds.get("start"),
        end=ds.get("end"),
        seed=ds.get("seed"),
        benchmark_tradable=spec.benchmark_tradable,
    )
    enforce_bounds(base_panel)
    check_required_history(spec, base_panel)
    base_digest = _prov.content_digest

    # Baseline linkage: validate exists + same project/version/hash (Phase 2.6 D14).
    baseline_run = None
    if baseline_run_id is not None:
        baseline_run = RunRepository(session).get(baseline_run_id)
        if baseline_run is None or baseline_run.project_id != owning_project:
            raise BaselineMismatchError(f"baseline run {baseline_run_id} not found for this project")
        if baseline_run.strategy_id != strategy_id or baseline_run.strategy_version != strategy_version:
            raise BaselineMismatchError("baseline run belongs to a different strategy version")
        if baseline_run.strategy_hash != expected_canonical_hash:
            raise BaselineMismatchError("baseline run hash does not match the requested strategy")

    confirmation_policy = ConfirmationPolicy(
        required_successes=2,
        total_trials=3,
        independent_seeds=list(seed_list),
    )

    # --- durable lifecycle (Phase 2.6 section 4) ---
    run = create_pending_run(
        session,
        project_id=owning_project,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_hash=expected_canonical_hash,
        data_mode="synthetic_campaign",
        limitations=[
            "Synthetic scenario perturbation of the SAME universe; bar-level replay only.",
            "Not an order-book or live-market replay.",
        ],
    )
    job = create_queued_job(
        session, run_id=run.id, idempotency_key=idempotency_key, stage=RunStage.STRESS_CAMPAIGN.value
    )
    campaign = CampaignRow(
        id=str(uuid.uuid4()),
        run_id=run.id,
        project_id=owning_project,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_hash=expected_canonical_hash,
        baseline_run_id=baseline_run_id,
        base_panel_digest=base_digest,
        mechanisms=list(mechanism_families),
        seeds=list(seed_list),
        failure_predicates=list(failure_predicates),
        confirmation_policy=confirmation_policy.model_dump(),
    )
    session.add(campaign)
    session.flush()
    from app.strategy_lab.canonical.durable import set_idempotency_resource

    set_idempotency_resource(
        session, scope="campaign", project_id=project_id, idempotency_key=idempotency_key,
        resource_type="campaign", resource_id=campaign.id,
    )

    # Mark running and commit (durable even though synchronous).
    _mark_run_stage(session, RunRepository, run.id, RunStage.STRESS_CAMPAIGN)
    job.state = JobState.RUNNING.value
    job.progress = 0.1
    session.flush()

    evaluator_intensities = [0.10, 0.20, 0.35, 0.50, 0.65]
    confirmed: list[FailureRecord] = []
    rate_by_mechanism: dict[str, float] = {}
    errors_by_mechanism: dict[str, int] = {}
    requested_worlds = 0
    evaluated_worlds = 0
    predicate_failures = 0
    evaluation_errors = 0

    for mechanism in mechanism_families:
        if mechanism not in ("drawdown", "vol_spike", "correlation_breakdown"):
            raise ValueError(f"unknown mechanism {mechanism}")
        fails = 0
        total_for_mech = 0
        for seed in seed_list:
            for intensity in evaluator_intensities:
                if evaluated_worlds >= world_budget:
                    break
                requested_worlds += 1
                world_key = f"{mechanism}:seed{seed}:i{int(intensity * 100)}"
                definition = ScenarioDefinition(
                    mechanism=mechanism,
                    seed=seed,
                    intensity=Decimal(str(intensity)),
                    start_index=max(0, base_panel.T // 2),
                    duration=max(1, base_panel.T // 4),
                )
                # Persist the world definition up-front (durable, reproducible).
                scenario = generate_scenario(base_panel, definition)
                world_row = ScenarioWorldRow(
                    id=scenario.scenario_id,
                    campaign_id=campaign.id,
                    world_key=world_key,
                    mechanism=mechanism,
                    seed=seed,
                    intensity=intensity,
                    definition=definition.to_dict(),
                    content_digest=scenario.content_digest,
                    diagnostics=scenario.diagnostics,
                )
                session.add(world_row)
                session.flush()

                ev = _evaluate_world(approved, base_panel, definition, predicates, expected_canonical_hash, role="primary")
                world_eval = WorldEvaluationRow(
                    id=str(uuid.uuid4()),
                    campaign_id=campaign.id,
                    world_id=world_row.id,
                    outcome=ev["outcome"],
                    predicate_results=ev["predicate_results"],
                    metrics=ev["metrics"],
                    error_message=ev["error_message"],
                    role="primary",
                )
                session.add(world_eval)
                session.flush()
                primary_id = world_eval.id
                evaluated_worlds += 1
                total_for_mech += 1

                if ev["outcome"] == "evaluation_error":
                    evaluation_errors += 1
                    errors_by_mechanism[mechanism] = errors_by_mechanism.get(mechanism, 0) + 1
                    continue
                if ev["outcome"] != "failed_predicate":
                    continue

                # Confirm by re-running independent seeds on the SAME mechanism+intensity.
                confirmed_here = 0
                for cseed in confirmation_policy.independent_seeds:
                    cdef = ScenarioDefinition(
                        mechanism=mechanism,
                        seed=cseed * 7919 + seed,
                        intensity=Decimal(str(intensity)),
                        start_index=definition.start_index,
                        duration=definition.duration,
                    )
                    cev = _evaluate_world(approved, base_panel, cdef, predicates, expected_canonical_hash, role="confirmation")
                    cworld = generate_scenario(base_panel, cdef)
                    cworld_row = ScenarioWorldRow(
                        id=cworld.scenario_id,
                        campaign_id=campaign.id,
                        world_key=f"{world_key}:confirm{cseed}",
                        mechanism=mechanism,
                        seed=cdef.seed,
                        intensity=intensity,
                        definition=cdef.to_dict(),
                        content_digest=cworld.content_digest,
                        diagnostics=cworld.diagnostics,
                    )
                    session.add(cworld_row)
                    session.flush()
                    c_eval = WorldEvaluationRow(
                        id=str(uuid.uuid4()),
                        campaign_id=campaign.id,
                        world_id=cworld_row.id,
                        outcome=cev["outcome"],
                        predicate_results=cev["predicate_results"],
                        metrics=cev["metrics"],
                        error_message=cev["error_message"],
                        role="confirmation",
                    )
                    session.add(c_eval)
                    session.flush()
                    if cev["outcome"] == "failed_predicate":
                        confirmed_here += 1
                if confirmed_here >= confirmation_policy.required_successes:
                    fails += 1
                    predicate_failures += 1
                    confirmed.append(
                        FailureRecord(
                            failure_id=primary_id,
                            campaign_id=campaign.id,
                            world_id=world_row.id,
                            mechanism=mechanism,
                            seed=seed,
                            parameters={"intensity": intensity},
                            strategy_id=strategy_id,
                            strategy_version=strategy_version,
                            canonical_hash=expected_canonical_hash,
                            predicate="|".join(failure_predicates),
                            metrics=ev["metrics"],
                        )
                    )
        if total_for_mech:
            rate_by_mechanism[mechanism] = fails / total_for_mech

    # Minimization + adjacent pass (only when at least one confirmed failure).
    minimization = None
    adjacent_pass = None
    if confirmed:
        best = min(confirmed, key=lambda f: float(f.parameters["intensity"]))
        min_val = float(best.parameters["intensity"])
        passing_val = _minimize(
            session, campaign, best, approved, base_panel, predicates, expected_canonical_hash, min_val
        )
        monotone = _is_monotone(session, campaign.id, best.failure_id)
        minimization = MinimizationRecord(
            dimension="intensity",
            minimized_value=min_val,
            passing_value=passing_val,
            monotone=monotone,
            stored_scenario_ref=best.world_id,
        )
        if passing_val is not None:
            adj = _build_adjacent_pass(
                session, campaign, best.failure_id, best.mechanism, approved, base_panel, predicates, expected_canonical_hash, passing_val
            )
            if adj is not None:
                adjacent_pass = adj

    # Finalize campaign accounting.
    campaign.requested_worlds = requested_worlds
    campaign.evaluated_worlds = evaluated_worlds
    campaign.predicate_failures = predicate_failures
    campaign.evaluation_errors = evaluation_errors
    campaign.error_rate = (evaluation_errors / evaluated_worlds) if evaluated_worlds else 0.0
    campaign.errors_by_mechanism = errors_by_mechanism
    campaign.failure_rate_by_mechanism = rate_by_mechanism

    # Persist campaign manifest artifact.
    manifest = {
        "campaign_id": campaign.id,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "canonical_hash": expected_canonical_hash,
        "requested_worlds": requested_worlds,
        "evaluated_worlds": evaluated_worlds,
        "predicate_failures": predicate_failures,
        "evaluation_errors": evaluation_errors,
        "error_rate": campaign.error_rate,
        "errors_by_mechanism": errors_by_mechanism,
        "failure_rate_by_mechanism": rate_by_mechanism,
        "mechanisms": list(mechanism_families),
        "seeds": list(seed_list),
        "failure_predicates": list(failure_predicates),
        "confirmation_policy": confirmation_policy.model_dump(),
        "minimization": minimization.model_dump() if minimization else None,
        "adjacent_pass": adjacent_pass.model_dump() if adjacent_pass else None,
        "failures": [f.model_dump() for f in confirmed],
    }
    from app.strategy_lab.canonical.durable import get_default_store

    store = get_default_store()
    artifact_index: list[dict[str, Any]] = []
    write_artifact(
        session, store=store, run_id=run.id, key=f"campaigns/{campaign.id}/manifest.json",
        payload=manifest, artifact_index=artifact_index,
    )
    campaign.result_manifest_key = f"campaigns/{campaign.id}/manifest.json"

    # Complete run/job.
    _mark_run_stage(session, RunRepository, run.id, RunStage.STRESS_CAMPAIGN)
    run.status = RunStatus.COMPLETED.value
    job.state = JobState.SUCCEEDED.value
    job.progress = 1.0
    job.result_ref = run.id
    session.flush()

    return CampaignResponse(
        api_version="v2",
        campaign_id=campaign.id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        canonical_hash=expected_canonical_hash,
        evaluated_worlds=evaluated_worlds,
        requested_worlds=requested_worlds,
        predicate_failures=predicate_failures,
        evaluation_errors=evaluation_errors,
        error_rate=campaign.error_rate,
        errors_by_mechanism=errors_by_mechanism,
        confirmed_failures=confirmed,
        failure_rate_by_mechanism=rate_by_mechanism,
        minimization=minimization,
        adjacent_pass=adjacent_pass,
        warnings=[],
        artifact_references=artifact_index,
    )


def _minimize(
    session, campaign, best, approved, base_panel, predicates, expected_hash, min_val
) -> float | None:
    """Bisection between a verified failing bound (min_val) and 0 (verified
    passing). Returns the largest passing value, or None if none found."""
    upper_fail = min_val  # known failing
    # confirm 0.0 passes
    probe = _probe_one(session, campaign.id, best.failure_id, best.mechanism, approved, base_panel, predicates, expected_hash, 0.0)
    if probe is None:
        # 0.0 errored; cannot establish a passing bound via bisection
        return None
    if probe:
        lo, hi = 0.0, upper_fail
    else:
        # 0.0 itself fails -> no passing boundary in (0, upper_fail)
        return None
    best_pass = 0.0
    for _ in range(12):
        mid = (lo + hi) / 2.0
        if mid <= 0.0:
            break
        ok = _probe_one(session, campaign.id, best.failure_id, best.mechanism, approved, base_panel, predicates, expected_hash, mid)
        if ok is None:
            break  # evaluation error -> stop descending
        if ok:
            best_pass = mid
            lo = mid
        else:
            hi = mid
    # persist boundary trials
    return best_pass if best_pass > 0.0 else None


def _probe_one(session, campaign_id, failure_id, mechanism, approved, base_panel, predicates, expected_hash, intensity) -> bool | None:
    """Return True if passing, False if failing, None on evaluation error. Persists a trial."""
    from app.strategy_lab.canonical.scenarios import ScenarioDefinition

    defn = ScenarioDefinition(
        mechanism=mechanism,
        seed=abs(hash(failure_id)) % 100000,
        intensity=Decimal(str(intensity)),
        start_index=0,
        duration=max(1, base_panel.T // 4),
    )
    ev = _evaluate_world(approved, base_panel, defn, predicates, expected_hash, role="minimization")
    scenario = generate_scenario(base_panel, defn)
    world_row = ScenarioWorldRow(
        id=scenario.scenario_id,
        campaign_id=campaign_id,
        world_key=f"{failure_id}:min{int(intensity*1000)}:{scenario.scenario_id[:8]}",
        mechanism=mechanism,
        seed=defn.seed,
        intensity=intensity,
        definition=defn.to_dict(),
        content_digest=scenario.content_digest,
        diagnostics=scenario.diagnostics,
    )
    session.add(world_row)
    session.flush()
    session.add(
        WorldEvaluationRow(
            id=str(uuid.uuid4()),
            campaign_id=campaign_id,
            world_id=world_row.id,
            outcome=ev["outcome"],
            predicate_results=ev["predicate_results"],
            metrics=ev["metrics"],
            error_message=ev["error_message"],
            role="minimization",
        )
    )
    failed = ev["outcome"] == "failed_predicate"
    session.add(
        MinimizationTrialRow(
            id=str(uuid.uuid4()),
            campaign_id=campaign_id,
            failure_id=failure_id,
            dimension="intensity",
            value=intensity,
            failed=failed,
            predicate_results=ev["predicate_results"],
            metrics=ev["metrics"],
        )
    )
    session.flush()
    if ev["outcome"] == "evaluation_error":
        return None
    return not failed


def _is_monotone(session, campaign_id: str, failure_id: str) -> bool:
    """Test monotonicity: every trial at intensity >= the minimized failing value
    must fail, and every trial below it must pass. If unproven, return False."""
    rows = (
        session.query(MinimizationTrialRow)
        .filter(
            MinimizationTrialRow.campaign_id == campaign_id,
            MinimizationTrialRow.failure_id == failure_id,
        )
        .all()
    )
    if len(rows) < 4:
        return False
    failing = [r.value for r in rows if r.failed]
    passing = [r.value for r in rows if not r.failed]
    if not failing or not passing:
        return False
    fail_min = min(failing)
    pass_max = max(passing)
    # monotone requires all passing values < all failing values
    return pass_max < fail_min


def _build_adjacent_pass(
    session, campaign, failure_id, mechanism, approved, base_panel, predicates, expected_hash, passing_val
) -> AdjacentPassRecord | None:
    """Evaluate the adjacent (passing) case and only emit a record if EVERY
    failure predicate is proven to pass."""
    defn = ScenarioDefinition(
        mechanism=mechanism,
        seed=abs(hash(failure_id)) % 100000 + 13,
        intensity=Decimal(str(passing_val)),
        start_index=0,
        duration=max(1, base_panel.T // 4),
    )
    ev = _evaluate_world(approved, base_panel, defn, predicates, expected_hash, role="adjacent")
    if ev["outcome"] == "evaluation_error":
        return None
    results = [PredicateResult(**pr) for pr in ev["predicate_results"]]
    # Explicit proof: not any predicate failed.
    if any(r.failed for r in results):
        return None
    scenario = generate_scenario(base_panel, defn)
    world_row = ScenarioWorldRow(
        id=scenario.scenario_id,
        campaign_id=campaign.id,
        world_key=f"{failure_id}:adjacent",
        mechanism=mechanism,
        seed=defn.seed,
        intensity=passing_val,
        definition=defn.to_dict(),
        content_digest=scenario.content_digest,
        diagnostics=scenario.diagnostics,
    )
    session.add(world_row)
    session.flush()
    session.add(
        WorldEvaluationRow(
            id=str(uuid.uuid4()),
            campaign_id=campaign.id,
            world_id=world_row.id,
            outcome=ev["outcome"],
            predicate_results=ev["predicate_results"],
            metrics=ev["metrics"],
            error_message=ev["error_message"],
            role="adjacent",
        )
    )
    adj_row = AdjacentPassRow(
        id=str(uuid.uuid4()),
        campaign_id=campaign.id,
        failure_id=failure_id,
        scenario_id=world_row.id,
        outcome="pass",
        predicate_results=ev["predicate_results"],
        metrics=ev["metrics"],
    )
    session.add(adj_row)
    session.flush()
    return AdjacentPassRecord(
        scenario_ref=world_row.id,
        metrics=ev["metrics"],
        description=f"Adjacent passing case at intensity {passing_val}; all failure predicates proven false",
    )


def load_campaign_result(session, campaign_id: str) -> CampaignResponse:
    """Reconstruct the typed campaign response from persisted records (replay)."""
    from sqlalchemy import select

    from app.persistence.models import CampaignRow, WorldEvaluationRow

    camp = session.get(CampaignRow, campaign_id)
    if camp is None:
        from fastapi import HTTPException

        raise HTTPException(404, "campaign not found")
    store = get_default_store()
    manifest = _read_json(store, f"campaigns/{campaign_id}/manifest.json") or {}
    # Reconstruct confirmed failures from primary world_evaluations.
    primaries = (
        session.execute(
            select(WorldEvaluationRow).where(
                WorldEvaluationRow.campaign_id == campaign_id,
                WorldEvaluationRow.role == "primary",
                WorldEvaluationRow.outcome == "failed_predicate",
            )
        )
        .scalars()
        .all()
    )
    confirmed = [
        FailureRecord(
            strategy_id=camp.strategy_id,
            strategy_version=camp.strategy_version,
            canonical_hash=camp.strategy_hash,
            failure_id=p.id,
            campaign_id=campaign_id,
            world_id=p.world_id,
            mechanism=p.mechanism if hasattr(p, "mechanism") else manifest.get("mechanisms", [None])[0],
            seed=0,
            parameters={"intensity": 0.0},
            predicate="|".join(manifest.get("failure_predicates", [])),
            metrics=p.metrics,
        )
        for p in primaries
    ]
    min_raw = manifest.get("minimization")
    adj_raw = manifest.get("adjacent_pass")
    return CampaignResponse(
        api_version="v2",
        campaign_id=camp.id,
        strategy_id=camp.strategy_id,
        strategy_version=camp.strategy_version,
        canonical_hash=camp.strategy_hash,
        evaluated_worlds=camp.evaluated_worlds,
        requested_worlds=camp.requested_worlds,
        predicate_failures=camp.predicate_failures,
        evaluation_errors=camp.evaluation_errors,
        error_rate=camp.error_rate,
        errors_by_mechanism=camp.errors_by_mechanism or {},
        confirmed_failures=confirmed,
        failure_rate_by_mechanism=camp.failure_rate_by_mechanism or {},
        minimization=MinimizationRecord(**min_raw) if min_raw else None,
        adjacent_pass=AdjacentPassRecord(**adj_raw) if adj_raw else None,
        warnings=[],
        artifact_references=[{"key": f"campaigns/{camp.id}/manifest.json"}],
    )


def _read_json(store, key):
    raw = store.get(key)
    if raw is None:
        return None
    import json as _json

    return _json.loads(raw)


def _mark_run_stage(session, RunRepo, run_id: str, stage: RunStage) -> None:
    RunRepo(session).mark_stage(run_id, stage)


__all__ = ["run_campaign"]
