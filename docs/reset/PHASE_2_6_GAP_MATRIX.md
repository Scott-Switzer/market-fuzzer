# Phase 2.6 — Integrity Closure Gap Matrix

Branch `reset/fenrix-product-reset-v1`; starting head `c6280d15a71f8428d1a0fdb6cea9948599ead8ae`.
Every defect from the Phase 2.6 assessment, its required behavior, affected files, tests, migration
need, and completion evidence. This document is the plan of record; each row is closed by a commit.

## Non-negotiable identity tuple
`(project_id, strategy_id, strategy_version, canonical_hash)` must be referenced by every durable
run, campaign, world, failure, minimization trial, adjacent pass, and audit record — or by an
unambiguous parent that does.

---

### D1 — Backtest results not persisted as evidence
- **current**: `run_backtest` returns `artifact_references=[]`; creates a `Run(COMPLETED)`/`Job(SUCCEEDED)` but writes no equity/trades/metrics/provenance/result JSON through `ArtifactStore`.
- **required**: write request/approved-strategy/provenance/metrics/equity/trades/exposures/cost/manifest artifacts atomically before marking succeeded; index each in `ArtifactIndexRow`; return populated `artifact_references`; add durable retrieval endpoints that verify every hash/size.
- **files**: `canonical/backtest_service.py`, `canonical/result_store.py` (new), `canonical/router.py`, `canonical/contracts.py`, `persistence/repositories.py` (RunRepository already indexes artifacts).
- **tests**: restart-retrieval, nonempty refs, hash verify, tamper detection, missing-artifact integrity error.
- **migration**: no (reuses `artifact_index`).
- **evidence**: `GET /runs/{id}/result` returns full metrics+trades+provenance after fresh process; tamper test raises.

### D2 — Campaign results/worlds/failures/minimization/adjacent not persisted; campaign_id inconsistency
- **current**: response computed in memory, `artifact_references=[]`; `FailureRecord.campaign_id` = idempotency key while `CampaignResponse.campaign_id` = run id.
- **required**: durable campaign/scenario/world/failure/minimization/adjacent records; one durable `campaign_id`; retrieval + replay endpoints reconstruct after restart.
- **files**: `persistence/models.py` (+CampaignRow, ScenarioWorldRow, WorldEvaluationRow, MinimizationTrialRow, AdjacentPassRow; FailureRow extended), `persistence/repositories.py`, `canonical/campaign_service.py`, `canonical/router.py`, `canonical/contracts.py`.
- **tests**: campaign reload, failure replay, campaign_id consistency, trials persisted.
- **migration**: YES (additive).
- **evidence**: `GET /campaigns/{id}`, `GET /failures/{id}/replay` after restart.

### D3 — Synthetic market transformations invalid
- **current**: drawdown/vol shocks modify only `close`; correlation_breakdown permutes close columns (reassigns one asset's closes to another symbol).
- **required**: typed `ScenarioDefinition`/`GeneratedScenario`; drawdown = deterministic negative-return path then rebuild full valid OHLCV; vol-spike operates on returns; correlation-breakdown preserves symbol identity via return-matrix transform; invariants asserted; unknown → 422.
- **files**: `canonical/scenarios.py` (new), `canonical/campaign_service.py`, `canonical/errors.py`.
- **tests**: per-mechanism determinism, symbol identity, OHLCV validity, digest stability, diagnostics.
- **migration**: no.
- **evidence**: scenario invariant tests green.

### D4 — Adjacent-pass validation incomplete
- **current**: records adjacent case without asserting the failure predicates actually pass.
- **required**: evaluate every predicate; only emit `AdjacentPassRecord` when `not any(p.failed)`.
- **files**: `canonical/campaign_service.py`.
- **tests**: still-failing adjacent candidate rejected; passing candidate proven.
- **evidence**: test asserting predicate results all false.

### D5 — Minimization not a verified algorithm
- **current**: checks three candidates then labels `monotone=True` untested.
- **required**: monotone mechanisms → bisection between verified passing/failing bounds; non-monotone → grid/local, `monotone=False`, output named `smallest_tested_failing_value`; persist every trial.
- **files**: `canonical/campaign_service.py`, `canonical/contracts.py`, `persistence/models.py`.
- **tests**: bisection returns first failing; monotonicity tested before flag; trials persisted; boundaries verified.
- **evidence**: unit test d=1 passes, d=2 fails → minimized=2.

### D6 — World-eval errors silently discarded
- **current**: broad `except Exception: continue`.
- **required**: per world → SUCCEEDED / FAILED_PREDICATE / EVALUATION_ERROR; report requested/evaluated/predicate-failures/errors/error-rate/errors-by-mechanism; a broken mechanism must not look safer.
- **files**: `canonical/campaign_service.py`, `canonical/contracts.py`.
- **tests**: injected eval error surfaces as EVALUATION_ERROR, not removed from denominator.

### D7 — Benchmark isolation not real
- **current**: demo builder appends benchmark to `panel.assets`; SPY can be both tradable and benchmark.
- **required**: `benchmark=None` → no benchmark; benchmark absent from `panel.assets` unless `benchmark_tradable`; reject SPY-as-both unless tradable; separate benchmark series identifier.
- **files**: `canonical/data_service.py`, `canonical/backtest_service.py`.
- **tests**: 4 benchmark cases in §18.3.

### D8 — Provenance partly inaccurate
- **current**: synthetic dates are every calendar day but labeled "actual trading calendar"; synthetic described with split/div policy; digest covers only close.
- **required**: weekday-only demo calendar; source-accurate adjustment/calendar/missing-data policy; digest over full panel (dates, ids+order, OHLCV, benchmark, metadata, source, policies).
- **files**: `canonical/data_service.py`.
- **tests**: §18.4.

### D9 — Audit record placeholder
- **current**: `data_source_digest = run.data_mode`; artifact hashes always empty; relationships unverified; compiler version hard-coded.
- **required**: verify approved+run+campaign+failure relationships; real data digest; enumerate+verify artifact hashes; actual compiler version; integrity error on mismatch.
- **files**: `canonical/evidence_service.py`, `canonical/router.py`, `canonical/contracts.py`.
- **tests**: §18.8 cross-project/tamper.

### D10 — Approval request fields ignored
- **current**: router passes neither `actor` nor `idempotency_key`; approval always records "user".
- **required**: thread `actor`+`idempotency_key`; record actor.
- **files**: `canonical/router.py`, `canonical/approval_service.py`.

### D11 — Approval dedup violates identity design
- **current**: dedup by canonical hash within project (conflates distinct logical strategies).
- **required**: idempotency key controls replay; identical content under distinct drafts/keys → distinct strategies allowed; never hash-alone dedup.
- **files**: `canonical/approval_service.py`, `persistence/models.py` (IdempotencyRecordRow).
- **migration**: YES.

### D12 — Tests don't prove claims
- **current**: "insufficient lookback" test runs full history; campaign test only checks adjacent object exists; missing cross-sectional/tactical vertical slices.
- **required**: real short-history 422; adjacent predicates-pass assertion; all five families with family-specific behavior.
- **files**: `tests/strategy_lab/canonical/*`.

### D13 — Executor-derived history requirements
- **current**: hard-coded approximate map in data_service.
- **required**: `minimum_history_requirements(spec) -> HistoryRequirements` on executor contract; contiguous ordered history check; structured 422 payload.
- **files**: `strategies/contracts.py`, `strategies/executors/*`, `canonical/data_service.py`.

### D14 — Baseline-run linkage unused
- **current**: `baseline_run_id` accepted, ignored.
- **required**: validate exists + same project/version/hash; use its panel/provenance as scenario base; store link; mismatch → 422.
- **files**: `canonical/campaign_service.py`.

### D15 — Durable run lifecycle
- **current**: Run created already COMPLETED, Job already SUCCEEDED.
- **required**: PENDING/QUEUED → RUNNING → persist+verify → COMPLETED/SUCCEEDED; failure path persists FAILED run/job in a compensating transaction not erased by request rollback.
- **files**: `canonical/backtest_service.py`, `canonical/campaign_service.py`, `canonical/tx.py` (new helper).

### D16 — API contract hardening + conditional distrust
- **current**: untyped project input; unconditional reasons-to-distrust list.
- **required**: typed `ProjectCreateRequest`; field validation; correct 409/404/422/500; warnings conditional on measured facts.
- **files**: `canonical/contracts.py`, `canonical/router.py`, `canonical/backtest_service.py`.

---

## Migrations
Single additive Alembic revision stacked on the existing head (NOT a regeneration — branch now has
multiple verified phases). Adds: `idempotency_records`, `campaigns`, `scenario_worlds`,
`world_evaluations`, `minimization_trials`, `adjacent_passes`, and new columns on `failures`.
Prove up/down on SQLite, upgrade+constraints on Postgres, `alembic check` clean.
