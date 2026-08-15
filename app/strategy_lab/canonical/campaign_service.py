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

from app.domain.failure import compute_failure_severity, confirmation_rate_lcb95
from app.domain.run import JobState, RunStage, RunStatus
from app.market_data.panel import MarketDataPanel
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
from app.strategy_lab.canonical.data_service import check_required_history, enforce_bounds
from app.strategy_lab.canonical.durable import (
    create_pending_run,
    create_queued_job,
    get_default_store,
    write_artifact,
)
from app.strategy_lab.canonical.errors import (
    BaselineMismatchError,
    ConfirmationIndependenceError,
    HashMismatchError,
)
from app.strategy_lab.canonical.predicates import (
    FailurePredicate,
    describe_predicate,
    evaluate_predicates,
    parse_predicates,
    predicates_failed,
)
from app.strategy_lab.canonical.scenarios import (
    ScenarioDefinition,
    assert_panel_invariants,
    effective_world_hash,
    generate_scenario,
    stable_seed,
)


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


def _derive_disjoint_confirmation_worlds(
    *,
    session,
    campaign_id: str,
    approved,
    base_panel: MarketDataPanel,
    base_digest: str,
    predicates: list[FailurePredicate],
    expected_canonical_hash: str,
    mechanism: str,
    seed: int,
    intensity: float,
    definition: ScenarioDefinition,
    primary_world_hash: str,
    world_key: str,
    confirmation_policy: ConfirmationPolicy,
) -> tuple[int, int, list[str]]:
    """Generate ``total_trials`` DISTINCT effective confirmation worlds.

    Returns ``(confirmed_here, confirmation_trials, confirmation_world_hashes)``.

    Every produced world's ``effective_world_hash`` must be disjoint from the
    primary search world AND from every other accepted confirmation world. On a
    collision we deterministically derive a fresh seed (an increasing ``attempt``
    salt folded into ``stable_seed``) and regenerate, up to a bounded budget. If
    the budget is exhausted before ``total_trials`` distinct worlds exist, raise
    ``ConfirmationIndependenceError`` -- the request cannot be satisfied without
    fabricating independence (e.g. a seed-invariant mechanism whose every seed
    yields the identical world).
    """
    total = confirmation_policy.total_trials
    if total <= 0:
        return 0, 0, []
    # Bounded, deterministic attempt budget: generous but finite, so a
    # seed-invariant mechanism terminates explicitly instead of looping forever.
    max_attempt = max(16, total * 8)
    seen: set[str] = {primary_world_hash}
    accepted: list[tuple[ScenarioDefinition, Any, str]] = []
    independent_seeds = confirmation_policy.independent_seeds
    for k in range(total):
        cseed = independent_seeds[k] if k < len(independent_seeds) else seed
        found = False
        for attempt in range(1, max_attempt + 1):
            cdef = ScenarioDefinition(
                mechanism=mechanism,
                seed=stable_seed(mechanism, seed, cseed, k, int(intensity * 1000), "confirm", attempt),
                intensity=Decimal(str(intensity)),
                start_index=definition.start_index,
                duration=definition.duration,
            )
            cworld = generate_scenario(base_panel, cdef)
            wh = effective_world_hash(base_digest, cdef, cworld.panel)
            if wh not in seen:
                seen.add(wh)
                accepted.append((cdef, cworld, wh))
                found = True
                break
        if not found:
            raise ConfirmationIndependenceError(
                f"could not generate {total} distinct confirmation worlds for "
                f"mechanism={mechanism!r} intensity={intensity}: every realization "
                f"collided with the primary or another confirmation world within "
                f"{max_attempt} deterministic attempts (the mechanism is "
                f"seed-invariant at this stress level, so independent confirmation "
                f"evidence is mathematically unavailable)"
            )
    # Evaluate + persist each distinct confirmation world exactly once.
    confirmed_here = 0
    confirmation_world_hashes: list[str] = []
    for cdef, cworld, wh in accepted:
        cev = _evaluate_world(
            approved, base_panel, cdef, predicates, expected_canonical_hash, role="confirmation"
        )
        cworld_row = ScenarioWorldRow(
            id=cworld.scenario_id,
            campaign_id=campaign_id,
            world_key=f"{world_key}:confirm{wh[:16]}",
            mechanism=mechanism,
            seed=cdef.seed,
            intensity=intensity,
            definition=cdef.to_dict(),
            content_digest=cworld.content_digest,
            world_hash=wh,
            diagnostics=cworld.diagnostics,
        )
        session.add(cworld_row)
        session.flush()
        c_eval = WorldEvaluationRow(
            id=str(uuid.uuid4()),
            campaign_id=campaign_id,
            world_id=cworld_row.id,
            outcome=cev["outcome"],
            predicate_results=cev["predicate_results"],
            metrics=cev["metrics"],
            error_message=cev["error_message"],
            role="confirmation",
        )
        session.add(c_eval)
        session.flush()
        confirmation_world_hashes.append(wh)
        if cev["outcome"] == "failed_predicate":
            confirmed_here += 1
    return confirmed_here, len(accepted), confirmation_world_hashes


def run_campaign(
    session,
    *,
    strategy_id: str,
    strategy_version: int,
    expected_canonical_hash: str,
    mechanism_families: list[str],
    seed_list: list[int],
    world_budget: int,
    failure_predicates: list[dict[str, Any]],
    project_id: str | None = None,
    baseline_run_id: str | None = None,
    data_source: dict | None = None,
    idempotency_key: str,
) -> CampaignResponse:
    from app.persistence.models import Strategy

    # Resolve the ACTUAL owning project first (Phase 2.6.1 gate 5).
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
        "mechanism_families": mechanism_families,
        "seed_list": seed_list,
        "world_budget": world_budget,
        "failure_predicates": failure_predicates,
        "baseline_run_id": baseline_run_id,
    }
    ir, created = reserve_idempotency(
        session,
        scope="campaign",
        project_id=owning_project,
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        resource_type="campaign",
        resource_id="",
        response_json={},
    )
    if not created:
        return _replay_or_reject_campaign(session, ir, idempotency_key)

    repo = StrategyRepository(session)
    approved = repo.get_approved_version(strategy_id, strategy_version)
    if approved is None:
        raise HashMismatchError(f"no approved version {strategy_id} (v{strategy_version})")
    if not approved.verify():
        raise HashMismatchError("stored approved version failed verification")
    if approved.canonical_hash != expected_canonical_hash:
        raise HashMismatchError("request hash != stored approved hash")

    spec = approved.to_spec()
    predicates = parse_predicates(failure_predicates)
    from app.strategy_lab.canonical.scenarios import validate_mechanisms

    validate_mechanisms(mechanism_families)

    ds = data_source or {
        "source": "demo_fixture",
        "universe": list(spec.universe),
        "benchmark": spec.benchmark,
    }
    # Acquire canonical market-data panel (Phase 3 cutover)
    from app.market_data.service import acquire_panel as acquire_canonical_panel

    canonical_base_panel, _quality = acquire_canonical_panel(
        data_source=ds,
        universe=ds.get("universe", list(spec.universe)),
        benchmark=ds.get("benchmark"),
        benchmark_tradable=spec.benchmark_tradable,
        allow_synthetic=ds.get("allow_synthetic", False),
    )
    enforce_bounds(canonical_base_panel)
    check_required_history(spec, canonical_base_panel)
    base_digest = canonical_base_panel.dataset_digest
    base_panel = canonical_base_panel

    # Baseline linkage: validate exists + same project/version/hash (Phase 2.6 D14).
    # When present, replay against the baseline's EXACT persisted input panel
    # (Phase 2.6.1 gate 7), NOT a freshly re-acquired one -- the campaign MUST
    # execute against the frozen dataset so generated worlds are perturbations
    # of it and no provider re-acquisition occurs (P5 Failure Lab depends on this).
    baseline_run = None
    if baseline_run_id is not None:
        baseline_run = RunRepository(session).get(baseline_run_id)
        if baseline_run is None or baseline_run.project_id != owning_project:
            raise BaselineMismatchError(f"baseline run {baseline_run_id} not found for this project")
        if baseline_run.strategy_id != strategy_id or baseline_run.strategy_version != strategy_version:
            raise BaselineMismatchError("baseline run belongs to a different strategy version")
        if baseline_run.strategy_hash != expected_canonical_hash:
            raise BaselineMismatchError("baseline run hash does not match the requested strategy")
        from app.market_data.artifacts import load_frozen_panel
        from app.market_data.errors import DatasetDigestMismatchError

        try:
            frozen_panel, manifest = load_frozen_panel(
                get_default_store(),
                baseline_run_id,
            )
        except DatasetDigestMismatchError as exc:
            raise BaselineMismatchError(f"baseline dataset digest mismatch: {exc}") from exc
        except Exception as exc:
            raise BaselineMismatchError(f"baseline panel reload failed: {exc}") from exc
        # Use the FROZEN panel for all downstream computation (no re-acquisition).
        base_panel = frozen_panel
        # Digest is taken from the frozen manifest, not a fresh acquisition, so
        # the self-check below is consistent and the persisted dataset_digest is
        # the frozen one's identity.
        base_digest = manifest["dataset_digest"]
        enforce_bounds(base_panel)
        check_required_history(spec, base_panel)

    # Confirmation policy: total_trials MUST equal the number of confirmation
    # trials actually executed per confirmed primary (one per seed in seed_list);
    # required_successes is a strict majority of those trials (Phase 2.6.1 gate 11).
    n_trials = len(seed_list)
    confirmation_policy = ConfirmationPolicy(
        required_successes=(n_trials // 2) + 1 if n_trials else 0,
        total_trials=n_trials,
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
        session,
        run_id=run.id,
        idempotency_key=idempotency_key,
        stage=RunStage.STRESS_CAMPAIGN.value,
        scope="campaign",
        project_id=owning_project,
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
        session,
        scope="campaign",
        project_id=owning_project,
        idempotency_key=idempotency_key,
        resource_type="campaign",
        resource_id=campaign.id,
    )

    # Mark running and commit BEFORE execution so the reservation + pending
    # campaign/run/job survive an execution crash (Phase 2.6.1 gate 2).
    _mark_run_stage(session, RunRepository, run.id, RunStage.STRESS_CAMPAIGN)
    job.state = JobState.RUNNING.value
    job.progress = 0.1
    session.flush()
    session.commit()

    evaluator_intensities = [0.10, 0.20, 0.35, 0.50, 0.65]

    try:
        return _execute_campaign_body(
            session,
            ir=ir,
            run=run,
            job=job,
            campaign=campaign,
            approved=approved,
            base_panel=base_panel,
            base_digest=base_digest,
            predicates=predicates,
            expected_canonical_hash=expected_canonical_hash,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            mechanism_families=mechanism_families,
            seed_list=seed_list,
            world_budget=world_budget,
            failure_predicates=failure_predicates,
            confirmation_policy=confirmation_policy,
            evaluator_intensities=evaluator_intensities,
        )
    except Exception as exc:
        # Persist FAILED lifecycle in a SEPARATE transaction (gate 3).
        session.rollback()
        _persist_failed_lifecycle(session, run.id, job.id, exc)
        raise


def _execute_campaign_body(
    session,
    *,
    ir,
    run,
    job,
    campaign,
    approved,
    base_panel,
    base_digest,
    predicates,
    expected_canonical_hash,
    strategy_id,
    strategy_version,
    mechanism_families,
    seed_list,
    world_budget,
    failure_predicates,
    confirmation_policy,
    evaluator_intensities,
):
    confirmed: list[FailureRecord] = []
    rate_by_mechanism: dict[str, float] = {}
    errors_by_mechanism: dict[str, int] = {}
    requested_worlds = 0
    evaluated_worlds = 0
    predicate_failures = 0
    evaluation_errors = 0
    # Phase 5 disjoint-evidence (item 1): an EFFECTIVE-WORLD identity may appear
    # at most once per campaign. Seed-invariant mechanisms (drawdown,
    # correlation_breakdown) ignore the per-world seed, so several
    # (mechanism, intensity, seed) tuples can realize the SAME effective world.
    # Also, minimization probes and the adjacent-pass world can revisit an
    # intensity already realized by another probe. Skip duplicate effective
    # worlds rather than crash on the unique constraint or count the same
    # evidence twice. The same set is consulted by primary, confirmation,
    # minimization, and adjacent-pass insertions.
    seen_world_hashes: set[str] = set()

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
                    seed=stable_seed(mechanism, seed, int(intensity * 1000)),
                    intensity=Decimal(str(intensity)),
                    start_index=max(0, base_panel.T // 2),
                    duration=max(1, base_panel.T // 4),
                )
                # Persist the world definition up-front (durable, reproducible).
                scenario = generate_scenario(base_panel, definition)
                # Canonical EFFECTIVE-WORLD identity (seed-excluded, content-derived,
                # tied to the frozen baseline digest). This is what confirmation
                # worlds must be provably disjoint from.
                primary_world_hash = effective_world_hash(base_digest, definition, scenario.panel)
                # Skip a PRIMARY world whose effective identity already appeared
                # earlier in this campaign (seed-invariant mechanism realized the
                # same world under a different seed): no duplicate evidence, no
                # unique-constraint crash.
                if primary_world_hash in seen_world_hashes:
                    continue
                seen_world_hashes.add(primary_world_hash)
                world_row = ScenarioWorldRow(
                    id=scenario.scenario_id,
                    campaign_id=campaign.id,
                    world_key=world_key,
                    mechanism=mechanism,
                    seed=seed,
                    intensity=intensity,
                    definition=definition.to_dict(),
                    content_digest=scenario.content_digest,
                    world_hash=primary_world_hash,
                    diagnostics=scenario.diagnostics,
                )
                session.add(world_row)
                session.flush()

                ev = _evaluate_world(
                    approved, base_panel, definition, predicates, expected_canonical_hash, role="primary"
                )
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

                # Confirm by re-running INDEPENDENT seeds on the SAME mechanism+intensity,
                # but ONLY on worlds that are provably DISJOINT from the primary search
                # world (and from each other) at the level of EFFECTIVE-WORLD identity.
                # This is the Phase 5 disjoint-evidence invariant: confirmation credit can
                # never be derived from the same effective world that discovered the
                # failure (nor from a duplicate confirmation world).
                #
                # Implementation: generate candidate confirmation worlds; compute each
                # one's effective_world_hash; reject any that collides with the primary or
                # with an already-accepted confirmation world; deterministically derive a
                # fresh seed (bounded attempt budget) and retry on collision. If the
                # requested number of distinct effective worlds cannot be produced within
                # the budget -- e.g. a seed-invariant mechanism like ``drawdown`` or
                # ``correlation_breakdown`` where every seed yields the SAME world -- the
                # campaign terminates explicitly (ConfirmationIndependenceError) rather
                # than fabricating independence by silently re-counting the primary world.
                (
                    confirmed_here,
                    confirmation_trials,
                    confirmation_world_hashes,
                ) = _derive_disjoint_confirmation_worlds(
                    session=session,
                    campaign_id=campaign.id,
                    approved=approved,
                    base_panel=base_panel,
                    base_digest=base_digest,
                    predicates=predicates,
                    expected_canonical_hash=expected_canonical_hash,
                    mechanism=mechanism,
                    seed=seed,
                    intensity=intensity,
                    definition=definition,
                    primary_world_hash=primary_world_hash,
                    world_key=world_key,
                    confirmation_policy=confirmation_policy,
                )
                if confirmed_here >= confirmation_policy.required_successes:
                    fails += 1
                    predicate_failures += 1
                    # Severity is derived from CONSEQUENCE: only the predicates
                    # that ACTUALLY failed (not every configured predicate).
                    pred_results = [PredicateResult(**pr) for pr in ev["predicate_results"]]
                    failed_pairs = [(p, r) for p, r in zip(predicates, pred_results, strict=True) if r.failed]
                    violated = [describe_predicate(p) for p, _ in failed_pairs]
                    # NOTE: raw per-predicate breach (observed value vs threshold) is
                    # retained in the evaluation record, but a generic cross-metric
                    # normalization is deliberately NOT folded into categorical severity.
                    severity = compute_failure_severity(
                        failed_predicate_names=violated,
                        confirmation_successes=confirmed_here,
                        confirmation_trials=confirmation_trials,
                    )
                    conf_rate = (confirmed_here / confirmation_trials) if confirmation_trials else 0.0
                    rate_lcb95 = confirmation_rate_lcb95(confirmed_here, confirmation_trials)
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
                            predicate="|".join(violated),
                            metrics=ev["metrics"],
                            severity=severity,
                            stress_intensity=float(intensity),
                            confirmation_trials=confirmation_trials,
                            confirmation_successes=confirmed_here,
                            confirmation_rate=conf_rate,
                            confirmation_rate_lcb95=rate_lcb95,
                            primary_world_hash=primary_world_hash,
                            confirmation_world_hashes=list(confirmation_world_hashes),
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
        # Load the EXACT original scenario definition for the confirmed failure;
        # minimization varies ONLY intensity, keeping mechanism/seed/start/
        # duration/params identical (Phase 2.6.1 gate 9).
        best_world = session.get(ScenarioWorldRow, best.world_id)
        base_def = ScenarioDefinition(
            mechanism=best_world.mechanism,
            # Use the EXACT seed recorded in the evaluated definition (the
            # derived world seed), not the raw request seed column.
            seed=int(best_world.definition["seed"]),
            intensity=Decimal(str(min_val)),
            start_index=int(best_world.definition["start_index"]),
            duration=int(best_world.definition["duration"]),
            parameters={k: Decimal(str(v)) for k, v in best_world.definition.get("parameters", {}).items()},
        )
        passing_val = _minimize(
            session,
            campaign,
            best,
            approved,
            base_panel,
            predicates,
            expected_canonical_hash,
            min_val,
            base_def,
            base_digest,
            seen_world_hashes,
        )
        monotone = _is_monotone(session, campaign.id, best.failure_id)
        minimization = MinimizationRecord(
            dimension="intensity",
            minimized_value=passing_val if passing_val is not None else min_val,
            passing_value=passing_val,
            monotone=monotone,
            stored_scenario_ref=best.world_id,
        )
        if passing_val is not None:
            adj = _build_adjacent_pass(
                session,
                campaign,
                best.failure_id,
                best.mechanism,
                approved,
                base_panel,
                predicates,
                expected_canonical_hash,
                passing_val,
                base_def,
                base_digest,
                seen_world_hashes,
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
    store = get_default_store()
    artifact_index: list[dict[str, Any]] = []
    write_artifact(
        session,
        store=store,
        run_id=run.id,
        key=f"campaigns/{campaign.id}/manifest.json",
        payload=manifest,
        artifact_index=artifact_index,
    )
    campaign.result_manifest_key = f"campaigns/{campaign.id}/manifest.json"

    # Complete run/job.
    _mark_run_stage(session, RunRepository, run.id, RunStage.STRESS_CAMPAIGN)
    run.status = RunStatus.COMPLETED.value
    job.state = JobState.SUCCEEDED.value
    job.progress = 1.0
    job.result_ref = run.id
    session.flush()

    response = CampaignResponse(
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
    # Persist the EXACT response for identical replay (Phase 2.6.1 gate 6).
    ir.response_json = response.model_dump(mode="json")
    session.flush()
    return response


def _minimize(
    session,
    campaign,
    best,
    approved,
    base_panel,
    predicates,
    expected_hash,
    min_val,
    base_def,
    base_digest,
    seen_world_hashes,
) -> float | None:
    """Bisection between a verified failing bound (min_val) and 0 (verified
    passing). Varies ONLY intensity; mechanism/seed/start/duration/params are
    taken from ``base_def`` (Phase 2.6.1 gate 9). Returns the largest passing
    value found, or None if none found. Also records the smallest failing value
    tested on ``best.parameters`` for accurate minimization reporting."""
    upper_fail = min_val  # known failing
    smallest_fail = min_val
    # confirm 0.0 passes
    probe = _probe_one(
        session,
        campaign.id,
        best.failure_id,
        best.mechanism,
        approved,
        base_panel,
        predicates,
        expected_hash,
        0.0,
        base_def,
        base_digest,
        seen_world_hashes,
    )
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
        ok = _probe_one(
            session,
            campaign.id,
            best.failure_id,
            best.mechanism,
            approved,
            base_panel,
            predicates,
            expected_hash,
            mid,
            base_def,
            base_digest,
            seen_world_hashes,
        )
        if ok is None:
            break  # evaluation error -> stop descending
        if ok:
            best_pass = mid
            lo = mid
        else:
            smallest_fail = min(smallest_fail, mid)
            hi = mid
    best.parameters["smallest_tested_failing_intensity"] = smallest_fail
    return best_pass if best_pass > 0.0 else None


def _probe_one(
    session,
    campaign_id,
    failure_id,
    mechanism,
    approved,
    base_panel,
    predicates,
    expected_hash,
    intensity,
    base_def,
    base_digest,
    seen_world_hashes,
) -> bool | None:
    """Return True if passing, False if failing, None on evaluation error. Persists a trial.

    The scenario is IDENTICAL to ``base_def`` except for the minimized dimension
    (intensity) (Phase 2.6.1 gate 9)."""
    from app.strategy_lab.canonical.scenarios import ScenarioDefinition

    defn = ScenarioDefinition(
        mechanism=base_def.mechanism,
        seed=base_def.seed,
        intensity=Decimal(str(intensity)),
        start_index=base_def.start_index,
        duration=base_def.duration,
        parameters=dict(base_def.parameters),
    )
    ev = _evaluate_world(approved, base_panel, defn, predicates, expected_hash, role="minimization")
    scenario = generate_scenario(base_panel, defn)
    wh = effective_world_hash(base_digest, defn, scenario.panel)
    # No duplicate effective evidence: if this probe's identity already exists
    # (e.g. the adjacent pass revisits the same intensity), the world row is
    # already persisted; still return the evaluated outcome.
    if wh not in seen_world_hashes:
        seen_world_hashes.add(wh)
        world_row = ScenarioWorldRow(
            id=scenario.scenario_id,
            campaign_id=campaign_id,
            world_key=f"{failure_id}:min{int(intensity * 1000)}:{scenario.scenario_id[:8]}",
            mechanism=mechanism,
            seed=defn.seed,
            intensity=intensity,
            definition=defn.to_dict(),
            content_digest=scenario.content_digest,
            world_hash=wh,
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
    session,
    campaign,
    failure_id,
    mechanism,
    approved,
    base_panel,
    predicates,
    expected_hash,
    passing_val,
    base_def,
    base_digest,
    seen_world_hashes,
) -> AdjacentPassRecord | None:
    """Evaluate the adjacent (passing) case and only emit a record if EVERY
    failure predicate is proven to pass. The scenario is IDENTICAL to the
    confirmed failure except for the minimized intensity (gate 9)."""
    defn = ScenarioDefinition(
        mechanism=base_def.mechanism,
        seed=base_def.seed,
        intensity=Decimal(str(passing_val)),
        start_index=base_def.start_index,
        duration=base_def.duration,
        parameters=dict(base_def.parameters),
    )
    ev = _evaluate_world(approved, base_panel, defn, predicates, expected_hash, role="adjacent")
    if ev["outcome"] == "evaluation_error":
        return None
    results = [PredicateResult(**pr) for pr in ev["predicate_results"]]
    # Explicit proof: not any predicate failed.
    if any(r.failed for r in results):
        return None
    scenario = generate_scenario(base_panel, defn)
    wh = effective_world_hash(base_digest, defn, scenario.panel)
    # No duplicate effective evidence: if this adjacent world's identity already
    # exists (e.g. it coincides with a minimization probe at the same intensity),
    # reuse the already-persisted world row; otherwise persist it once.
    if wh in seen_world_hashes:
        world_row = session.query(ScenarioWorldRow).filter_by(campaign_id=campaign.id, world_hash=wh).first()
        if world_row is None:  # defensive: identity seen but row gone
            seen_world_hashes.discard(wh)
    if wh not in seen_world_hashes:
        seen_world_hashes.add(wh)
        world_row = ScenarioWorldRow(
            id=scenario.scenario_id,
            campaign_id=campaign.id,
            world_key=f"{failure_id}:adjacent",
            mechanism=mechanism,
            seed=defn.seed,
            intensity=passing_val,
            definition=defn.to_dict(),
            content_digest=scenario.content_digest,
            world_hash=wh,
            diagnostics=scenario.diagnostics,
        )
        session.add(world_row)
        session.flush()
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
    """Reconstruct the typed campaign response from persisted records (replay).

    Prefers the persisted manifest's faithful ``failures`` list (each a full
    ``FailureRecord`` with the ACTUAL mechanism/seed/intensity/metrics recorded
    during evaluation) over re-deriving placeholders (Phase 2.6.1 gate 6/7).
    Verifies the manifest artifact hash before trusting it (gate 12).
    """
    from app.persistence.models import CampaignRow
    from app.strategy_lab.canonical.durable import verify_artifacts

    camp = session.get(CampaignRow, campaign_id)
    if camp is None:
        from fastapi import HTTPException

        raise HTTPException(404, "campaign not found")
    store = get_default_store()
    # Verify artifact integrity for the underlying run before trusting the manifest.
    verify_artifacts(session, store=store, run_id=camp.run_id)
    manifest = _read_json(store, f"campaigns/{campaign_id}/manifest.json") or {}
    confirmed = [FailureRecord(**f) for f in manifest.get("failures", [])]
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


def _persist_failed_lifecycle(session, run_id: str, job_id: str, exc: Exception) -> None:
    """Persist FAILED campaign run/job in a SEPARATE transaction (survives the
    request rollback; the pending rows were committed before execution)."""
    from app.persistence.models import JobRow, RunRow

    try:
        run = session.get(RunRow, run_id)
        job = session.get(JobRow, job_id)
        if run is not None:
            run.status = RunStatus.FAILED.value
        if job is not None:
            job.state = JobState.FAILED.value
            job.failure_json = {
                "code": "campaign_execution_error",
                "message": str(exc),
                "retryable": False,
            }
            job.progress = 1.0
        session.commit()
    except Exception:  # pragma: no cover - best-effort failure persistence
        session.rollback()


def _replay_or_reject_campaign(session, ir, idempotency_key: str) -> CampaignResponse:
    """Handle a non-creating reservation: complete replay, failed prior, or in-flight."""
    from app.persistence.models import CampaignRow, RunRow
    from app.strategy_lab.canonical.errors import IdempotencyInFlightError, PriorAttemptFailedError

    if ir.response_json:
        return CampaignResponse.model_validate(ir.response_json)
    if ir.resource_id:
        camp = session.get(CampaignRow, ir.resource_id)
        if camp is not None:
            run = session.get(RunRow, camp.run_id)
            if run is not None and run.status == RunStatus.FAILED.value:
                raise PriorAttemptFailedError(
                    f"idempotency key {idempotency_key!r} previously failed; use a new key to retry"
                )
            if run is not None and run.status == RunStatus.COMPLETED.value:
                return load_campaign_result(session, ir.resource_id)
        raise IdempotencyInFlightError(
            f"idempotency key {idempotency_key!r} is already reserved for an in-flight campaign"
        )
    raise IdempotencyInFlightError(
        f"idempotency key {idempotency_key!r} is already reserved for an in-flight campaign"
    )


__all__ = ["run_campaign", "load_campaign_result"]
