# Agent handoff: next Synthetic Market World block

Date: 2026-07-18
Repository: `Scott-Switzer/market-fuzzer`
Checkout: `/Users/scottthomasswitzer/Documents/OAI_Build_Week`
Branch: `codex/enterprise-stress-lab-ui`
Current head: `4d7c34a`
Open review target: PR #5, Strategy Stress Lab UI

## Product direction

The product is pivoted toward enterprise Synthetic Market World infrastructure
for prop shops and quant teams. Education and the original Arena remain as
secondary surfaces. Deterministic application code is authoritative; GPT must
not determine simulation outcomes. Claims must remain bounded to declared
synthetic configurations and must not imply profitability, live-market fidelity,
best execution, privacy, or production readiness without evidence.

## What exists

- Versioned Synthetic Market World registry with manifest hashes and audit events.
- Registered scenario packs with bounded intervention types.
- Calibration packs, calibration runs, accepted parameter sets, and ensemble
  provenance attached to registered worlds.
- Deterministic scenario compilation and Strategy Stress Lab experiment APIs.
- Governed validation reports with fit-for-use vectors, evidence manifests,
  uncertainty summaries, report hashes, and JSON export.
- Stress Lab UI at `/strategy-stress-lab`.
- Persisted experiment jobs with queued/running/completed/failed state,
  progress fields, resume endpoint, job listing, and durable hashed artifacts.
- Artifact endpoint:
  `GET /api/enterprise/experiment-jobs/{job_id}/artifacts/{kind}`.
- Enterprise experiment benchmark filtering by requested policy IDs and the
  relevant latency-stress variant.
- Explicit order/trade serializers replacing recursive `dataclasses.asdict()`
  in the hot path.

## Verified state

- Ruff passes.
- Mypy passes across 51 source files.
- `git diff --check` passes.
- Focused exchange tests pass.
- The end-to-end Strategy Stress Lab test passes, including job creation,
  resume, progress, artifact retrieval, pagination, validation, and export.
- Full pytest collection discovers 127 tests. A full run began executing but did
  not emit a final summary in the local runner; rerun it with the repo venv
  before claiming the suite is green.
- The simulator is deterministic and currently intentionally single-threaded.
  An attempted thread-pool implementation was reverted after it caused a
  hanging test path.

## Next implementation blocks

### 1. Complete verification and performance budget

Run the full suite with `/tmp/oai-build-week-venv/bin/python -m pytest -q`.
Measure one-policy/one-variant and enterprise end-to-end latency. Add a
performance regression test with a generous machine-independent budget or a
relative baseline. Do not reintroduce threads without an explicit worker
boundary and deterministic regression coverage.

### 2. True cell-level resumability

Current jobs are persisted, but resume reruns the whole experiment. Add a
`experiment_cells` table keyed by job, strategy, scenario/world hash, and seed.
Persist each cell status and result artifact transactionally. Resume should
skip completed cells, recompute only pending/failed cells, and derive the final
experiment result from cells in stable sorted order.

### 3. Execute declared scenario packs directly

The current enterprise path still uses the older Arena benchmark for strategy
rows and only partially executes compiled protected worlds. Make the declared
scenario pack the primary execution input. Keep the Arena benchmark only as an
explicit baseline comparator. Persist baseline and protected cell provenance,
world hashes, seeds, and compile hashes separately.

### 4. External strategy adapter contract

Implement a bounded `external_adapter` contract. Start with a deterministic
in-process adapter interface; reject arbitrary uploaded Python. Define version,
input observation schema, output action schema, timeout/error behavior, and
adapter hash. Store adapter provenance in experiments and reports.

### 5. Durable artifact manifests and UI operations

Add an artifact manifest containing job ID, experiment ID, artifact kind,
content hash, schema version, source world/scenario hashes, seeds, creator, and
created time. Expose it in the Stress Lab UI with job progress, failure state,
resume action, and report/artifact download links.

### 6. Regression suites for approved worlds

Allow a governed world/scenario pack to declare regression cases and expected
invariants. Run them through the same cell engine, persist pass/fail evidence,
and prevent a governed release when required invariants fail.

## Important files

- `app/api/app.py`: enterprise routes and experiment orchestration.
- `app/execution_store.py`: SQLite schema and durable registry/job/artifact state.
- `app/execution_arena.py`: legacy Arena benchmark and policy matrix.
- `app/scenario_studio.py`: deterministic scenario compilation.
- `app/governance.py`: governed report and verdict construction.
- `app/simulation.py`: synthetic exchange simulation hot path.
- `app/exchange/orders.py`: optimized order/trade serialization.
- `app/static/stress-lab.html`: browser-facing Stress Lab.
- `tests/test_synthetic_market_registry.py`: enterprise integration coverage.
- `docs/ENTERPRISE_PRODUCT.md`: product positioning and roadmap.

## Review and delivery protocol

Work in focused milestone commits on the current branch. Run Ruff, mypy,
focused tests, full pytest, and `git diff --check`. Push to PR #5 so Greptile
can review. Do not claim CI or Greptile approval until GitHub confirms it.

