# Strategy Validation Lab — Minimum Coherent UI Design

## 0. North Star
The Strategy Validation Lab is a guided, gate-based workflow that turns a strategy description into a sealed evidence package. It reuses the existing Break Test, World Registry, and Sealed Campaign surfaces rather than replacing them.

## 1. Screen Map (6 Screens)
| Screen | Primary Purpose | Legacy Surface Absorbed | New File |
|--------|-----------------|--------------------------|----------|
| S1 Strategy Brief | Describe strategy, compile clauses, resolve ambiguity, approve/lock | partial break-test step-1 plain-English + Stress Lab brief compiler | `app/static/strategy-lab.html` |
| S2 Data & Universe | Select data source, universe, execution/cost model | break-test step-1 data/universe/cost sections | `app/static/strategy-lab.html` |
| S3 Historical Backtest | Run historical baseline, review report | break-test step-2/4 | `app/static/strategy-lab.html` |
| S4 Sealed Synthetic | Start sealed synthetic test, monitor job, review failures | Stress Lab experiment form + job panel + sealed-campaign summary | `app/static/strategy-lab.html` |
| S5 Replay & Minimized Evidence | Open minimized replay, review suggestions | break-test `equity-chart` + replay + correction UI + `synthetic_release_validation_report.json` | `app/static/strategy-lab.html` |
| S6 Evidence Export | Export evidence package in scoped envelope | break-test export + stress-lab report download + `EvaluationEvidenceV1` scopes | `app/static/strategy-lab.html` |

Arena pages (`arena.html`, `arena.js`) are unchanged.

## 2. API Contracts

### 2.1 Strategy Brief & Lock
- `POST /api/enterprise/strategies/compile-brief`
  - Request: `{ brief: string }`
  - Response: `{ compiler_version, input_checksum, normalized_brief, matched_intents, ambiguities, requires_confirmation, claim_boundary, proposal }`
- `POST /api/enterprise/strategies`
  - Request: `{ name, description, strategy_type, builtin_policy_id?, external_adapter? }`
  - Response: durable strategy registry record including `strategy_id`, `version_label`, `actor`, `created_at`.
- `POST /api/enterprise/sealed-campaigns`
  - Request: `{ strategy_id, same_family_ids, holdout_family_ids, worlds_per_family, hidden_parameter_ranges, scoring_policy_digest, instruments, steps }`
  - Response: `campaign_id`, `public_commitment`, `created_at`, `state="draft|prepared"`.
- `POST /api/enterprise/sealed-campaigns/{campaign_id}/freeze`
  - Response: updated campaign record with `state="frozen"` and frozen artifact digest.
- `POST /api/enterprise/sealed-campaigns/{campaign_id}/finalize`
  - Response: finalized campaign record with `primary_result` digest.

### 2.2 Data & Universe
- `GET /api/enterprise/worlds`
- `POST /api/enterprise/worlds`
- `GET /api/enterprise/scenario-packs`
- `POST /api/enterprise/scenario-packs`
- `POST /api/break-test/run`
  - Request: `BreakTestRequest` (`data_source`, `closes`, `strategy_type`, `params`, `worlds_per_regime`, `forward_mode`, `strategy_code`, `tcost_model_*`, etc.)
  - Response: single run result including `session_id`, `historical`, `forward_test`, `failure_analysis`, `correction_suggestion`.
- `GET /api/break-test/session/{session_id}`
- `GET /api/break-test/session/{session_id}/export?format=html|pdf`

### 2.3 Sealed Synthetic Test
- `POST /api/enterprise/experiment-jobs`
  - Request: `{ name, strategy_ids, scenario_pack_id, seeds }`
  - Response: `job_id`, `progress`, `state`, optional `artifact`.
- `POST /api/enterprise/experiment-jobs/{job_id}/resume`
  - Response: completed job progress + artifact reference.
- `GET /api/enterprise/experiment-jobs/{job_id}`
- `GET /api/enterprise/experiments/{experiment_id}`
- `GET /api/enterprise/experiments/{experiment_id}/validate`
- `POST /api/enterprise/experiments/{experiment_id}/validate`
- `GET /api/enterprise/experiments/{experiment_id}/validation`
- `GET /api/enterprise/experiments/{experiment_id}/validation/export`
- `GET /api/enterprise/experiments/{experiment_id}/artifacts/experiment-result`
- `GET /api/enterprise/experiments/{experiment_id}/artifacts/experiment-result/download`
- `GET /api/enterprise/decision-benchmark`
  - Optional deterministic demo fixture for readiness screen.

### 2.4 Evidence Envelope
- `EvaluationEvidenceV1` scopes already implemented:
  - `development_fixture`: break-test / deterministic stress-lab results.
  - `adaptive_diagnostic`: failure-surface / minimized replay results.
  - `sealed_primary`: campaign-finalized primary evaluation results.
- Export endpoint: reuse `/api/break-test/session/{id}/export` plus stress-lab validation JSON download.

## 3. Page-State Contracts

### 3.1 Shared State Shape
```json
{
  "screen": "brief|data|backtest|sealed|replay|export",
  "strategy": { "strategy_id": null, "strategy_type": null, "policy": null, "locked": false },
  "campaign": { "campaign_id": null, "state": null, "commitment": null },
  "world": { "world_id": null, "pack_id": null },
  "backtest": { "session_id": null, "status": null, "result": null },
  "sealed": { "job_id": null, "experiment_id": null, "status": null, "failures": [], "report": null },
  "playback": { "replay_id": null, "minimized": false, "frames": [] },
  "evidence": { "exported": false, "artifacts": [], "validation": null }
}
```

### 3.2 Screen State Details
- **S1 Strategy Brief**: `strategy.draft`, ambiguities[], suggestions[], compileError.
- **S2 Data & Universe**: `world.candidates[]`, `data.validation`, `data.short_history`.
- **S3 Backtest**: `backtest.status="queued|complete|failed"`, `backtest.result.historical`, `backtest.result.forward_test`, `backtest.result.failure_analysis`, `backtest.result.correction_suggestion`.
- **S4 Sealed**: `sealed.status`, `sealed.progress{percent,cells,total}`, `sealed.failures[]`, `sealed.report`.
- **S5 Replay**: `playback.minimized_failure_id`, `playback.timeline[]`, `playback.evidence_rows[]`, `suggestions[]`.
- **S6 Export**: `evidence.package_urls[]`, `evidence.validation_scope`, `evidence.limitations[]`.

## 4. Frontend File Targets
- **New**: `app/static/strategy-lab.html` — single-page application containing 6 screens, state machine, and shared layout.
- **Reuse unchanged**: `app/static/break-test.html`, `app/static/synthetic-market-world.html`, `app/static/stress-lab.html`, `app/static/arena.html`, `app/static/arena.js`.
- **Backends unchanged**: all existing `/api/break-test/*`, `/api/enterprise/*`, and experiment/job endpoints remain untouched.
- **Legacy route preservation**:
  - `/` → existing index
  - `/break-test` → existing break-test UI
  - `/synthetic-market-world` → existing world registry
  - `/strategy-stress-lab` → existing stress lab
  - `/arena` → existing teaching arena
  - **New**: `/strategy-lab` → new Strategy Validation Lab landing/entry point
  - **New**: `/api/strategy-lab/evidence/summary` thin wrapper if needed; otherwise reuse existing APIs directly from new UI.

## 5. Wireframe & Interaction Note
The six screens share navigation rail:
1. Brief → 2. Data → 3. Backtest → 4. Sealed → 5. Replay → 6. Export

Gates:
- Data screen is the first place that can submit `/api/break-test/run`.
- Sealed screen is enabled only after backtest completes.
- Export screen becomes available after either sealed finalize or replay review completes.

Failure annotation: screen 4 inserts `failure_surface.json` semantics from existing backend into a failure list. Screen 5 opens minimized frames similar to arena.js `renderReplay`, but scoped to one failure id and one world hash.

Evidence export: screen 6 calls existing backend export routes and packages JSON into `EvaluationEvidenceV1` envelope with explicit scope and claim boundary.

## 6. Legacy Preservation Plan
- No legacy HTML file is moved or renamed.
- No `/api/break-test` or `/api/enterprise` route is removed or retagged.
- New routes are additive:
  - `GET /strategy-lab`
  - optional `GET /strategy-lab/run/{run_id}` redirect/alias to break-test historic session or experiment detail.
- Existing `` can link `/strategy-lab` as the product entry point; internal links still work.

## 7. Minimal Implementation Order
1. create `app/static/strategy-lab.html` scaffold with shared header/nav and state machine
2. implement screens 1-2 with existing compile-brief and enterprise world/scenario forms
3. implement screen 3 wiring to `/api/break-test/run` and export endpoints
4. implement screens 4-6 wiring to experiment jobs, replay data, and evidence envelope
5. add README note and `/strategy-lab` route binding in `app/api/app.py`

## 8. Key Design Decisions
- Reuse `EvaluationEvidenceV1` scopes and `development_fixture_evidence` helper rather than invent new evidence state.
- Reuse arena.js SVG replay logic; do not reimplement charting.
- keep “minimum coherent” constraint: no unrelated challenge-arena changes.
- keep legacy `/arena` and `/market-fuzzer` fully untouched.
