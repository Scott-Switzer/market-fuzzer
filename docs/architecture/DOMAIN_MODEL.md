# Fenrix Domain Model

This document describes the actual domain contracts in `app/domain/`, the
persistence layer in `app/persistence/`, and the artifact store in
`app/evidence/`. All types are Pydantic v2 (domain) or SQLAlchemy 2 (rows).

## Dependency direction
Everything points inward toward `app/domain`. Persistence and evidence depend on
domain; domain depends on nothing app-specific.

```
api / jobs / compiler / historical / synthetic / exchange
        |                 |
        v                 v
   app/persistence    app/evidence
        \                 /
         v               v
            app/domain   (StrategySpec, Run, Job, failures, versions, data_source)
```

## Core contracts (`app/domain/`)

### `strategy_spec.py` — `StrategySpec` (the keystone)
Canonical, versioned strategy contract (schema `strategy-spec/v1.1`). See
ADR-0004 and ADR-0007. Key points:
- **Identity ≠ content.** `strategy_id` is a standalone UUID4 (never the content
  hash); `strategy_version` is a monotonic int within a `strategy_id`.
- `canonical_dict()` / `canonical_json()` — normalized serialization with
  `VOLATILE_KEYS` removed and `Decimal` values canonicalized.
- `compute_hash()` — sha256 over the canonical JSON. `canonical_hash` is a
  **computed property** (no stored field ⇒ no drift).
- `blocking_reasons(available_symbols=None, supported_types=None)` — fail-closed
  execution gate. A type is executable only if it is in `supported_types`
  (i.e. has a registered executor — see ADR-0009).
- `is_executable(...)` — convenience boolean.

Contract-level numbers (weights, exposures, cost bps, thresholds that enter the
hash) are `Decimal`; floats are used only at the NumPy execution boundary.

Enums: `StrategyType` (cross_sectional_factor, long_only_ranking,
time_series_signal, static_allocation, tactical_allocation, unsupported),
`AssetClass`, `Frequency`, `Weighting`, `ExecutionTiming`, `ClauseState`.
Nested models: `Clause`, `CostModel`, `OrderPolicy` (typed; `allow_same_bar`
must be explicitly true for `SAME_CLOSE`), `PortfolioConstruction` (selection +
weighting + `target_weights`), `RiskConstraints` (the SOLE home for
gross/net/position exposure — no duplication).
`benchmark_tradable` (default False) governs whether the benchmark may appear in
the tradable universe.

### `strategy_version.py` — `DraftStrategy` + `ApprovedStrategyVersion`
- `DraftStrategy` — a mutable-by-reconstruction draft carrying a stable
  `strategy_id` + `version`. `next_version(new_spec)` preserves identity and
  bumps the version.
- `ApprovedStrategyVersion` — **immutable** (`frozen=True`) snapshot. Stores the
  canonical JSON **string** + hash as the source of truth, so nested mutation is
  structurally impossible. Built via `model_validate` (never
  `model_copy(update=...)`, which Pydantic does not validate). `to_spec()`
  reconstructs the executable spec and re-verifies the hash (raises on tamper);
  `verify()` returns a bool. See ADR-0007.

### `schema_migration.py` — v1 → v1.1 converter
`convert_v1_to_v1_1(doc)` upgrades a legacy v1 spec dict: mints a fresh UUID
identity, moves exposure fields into `risk_constraints`, defaults
`benchmark_tradable=False`, and **refuses to silently drop** unknown keys.

### `run.py` — `Run`, `Job`, state machine
- `RunStage` — the 12 stages mirroring the OpenTelemetry trace tree.
- `JobState` — queued → running → {succeeded, failed, cancelled}; failed → queued
  (retry). `succeeded`/`cancelled` are terminal. Transitions validated by
  `can_transition`; illegal transitions raise `IllegalTransition`.
- `Job` — durable unit of work with `idempotency_key`, bounded `max_attempts`,
  `progress`, structured `FailureReason` (with `retryable` flag). `can_retry()`
  only true for transient (retryable) failures within budget — deterministic
  validation errors are never retried (gate: no retry for deterministic errors).
- `Run` — a full validation run of a locked version; tracks `stages_completed`
  idempotently.

### `failure.py` — confirmed failures + minimization
- `ConfirmedFailure` — mechanism, intensity, violated predicates, seed
  agreement, severity, `execution_sensitive` (routes to Tier-B).
- `MinimizedBoundary` — `check_invariant()` enforces gate 14 (a still-failing
  minimized value must be strictly beyond the passing lower bound).
- `AdjacentPass` — `found` is False when none exists; fabricating one is gate 15.

### `data_source.py` — data provenance
- `DataTier` (fenrix_bundle / yfinance / synthetic_fixture) and
  `DataProvenance` (with `dates_are_relative`, `point_in_time` flags) so a
  synthetic panel is never presented as historical (gate 5) and a relative-date
  bundle is never presented as point-in-time.

## Persistence (`app/persistence/`)
- `models.py` — SQLAlchemy 2 declarative rows with a metadata **naming
  convention** (pk/fk/uq/ck/ix). Tables: `Project`, `Strategy`,
  `StrategyVersionRow` (stores immutable `canonical_json`; `UNIQUE(strategy_id,
  version)`; index on `canonical_hash`), `RunRow` (composite FK to
  `strategy_versions(strategy_id, version)`), `JobRow` (unique `idempotency_key`;
  CHECK constraints on attempts/progress and state/stage allow-lists derived from
  the domain enums), `FailureRow` (indexed on run_id/strategy_hash/mechanism),
  `ArtifactIndexRow` (`UNIQUE(run_id, store, artifact_key)`),
  `EvidenceVerificationRow` (one row per run — latest result only),
  `DataSourceRow`. See ADR-0008.
- `database.py` — engine/session factory; `FENRIX_DATABASE_URL` (Postgres in
  prod, SQLite default for dev/tests); SQLite connections get
  `PRAGMA foreign_keys=ON`; `session_scope` transactional context.
- `repositories.py` — the sanctioned read/write path exchanging domain objects.
  `StrategyRepository.add_approved_version`/`get_approved_version` persist and
  reconstruct the immutable snapshot byte-for-byte. `JobRepository.submit` is
  **concurrency-safe idempotent**: it inserts inside a SAVEPOINT and, on
  `IntegrityError` from the unique constraint (a concurrent inserter won the
  race), rolls back only that savepoint and returns the existing row — never two
  jobs, never a 500 on a duplicate. Full job/run round-trip fidelity: every
  field (including timestamps and `inputs_frozen`) is restored.
- `migrations/` — Alembic; the regenerated initial migration creates all 9 tables
  with all constraints. Round-trip (upgrade→downgrade→upgrade) proven on SQLite
  (`tests/integration/test_migrations.py`) and on real PostgreSQL
  (`tests/integration/test_postgres_persistence.py`); `alembic check` runs in CI.

## Artifact store (`app/evidence/`)
- `artifact_store.py` — `ArtifactStore` ABC + `Filesystem`/`InMemory` impls and
  `ArtifactRef`. See ADR-0006. Public serialization contains no filesystem path.
  Path containment uses `Path.is_relative_to` (not string prefixing) and rejects
  symlink escapes and empty keys. Writes are atomic + durable: unique temp file
  in the target dir, `flush` + `os.fsync`, then `os.replace`. `handle()` returns
  an opaque `artifact://` handle (NOT a signed URL).

## Strategy execution registry (`app/strategies/`)
- `registry.py` — `StrategyRegistry` / `default_registry`. "Supported" means a
  concrete executor is registered. `supported_types()` feeds the domain approval
  gate; `validate_complete()` fails if any advertised type lacks an executor. No
  discovery/scanning — registration is explicit. See ADR-0009. Concrete
  executors land in Phase 2 (`app/strategies/executors/`).
- `contracts.py` — `StrategyExecutor` protocol, `StrategyExecutionContext`,
  `TargetPlan` (T×N target weights decided at decision-bar close; executed at the
  next open by the generic accounting engine).

## Test coverage
- `tests/contract/` — StrategySpec hash/identity/gating/Decimal (38), run/job
  state machine (11), failure + versioning/immutability (13), schema converter (5).
- `tests/evidence/` — artifact store incl. containment/symlink/atomic (32).
- `tests/strategies/` — registry + support gate (7).
- `tests/integration/` — persistence + repositories (8), migrations (3),
  real-Postgres FK/CHECK/idempotency/tz (8, skipped without a Postgres URL).
