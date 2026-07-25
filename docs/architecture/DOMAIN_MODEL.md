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
Canonical, versioned strategy contract (schema `strategy-spec/v1`). See
ADR-0004. Key methods:
- `canonical_dict()` / `canonical_json()` — normalized serialization with
  `VOLATILE_KEYS` removed.
- `compute_hash()` — sha256 over the canonical JSON; assigned to
  `canonical_hash` on every validation.
- `blocking_reasons(available_symbols=None)` — fail-closed execution gate.
- `is_executable(...)` — convenience boolean.

Enums: `StrategyType` (cross_sectional_factor, long_only_ranking,
time_series_signal, static_allocation, tactical_allocation, unsupported),
`AssetClass`, `Frequency`, `Weighting`, `ExecutionTiming`, `ClauseState`.
Nested models: `SignalDefinition`, `Clause`, `CostModel`,
`PortfolioConstruction`, `RiskConstraints`.

### `strategy_version.py` — `StrategyVersion`
Immutable approved snapshot. `from_spec()` builds a draft; `lock(approved_by)`
re-verifies the hash matches spec content AND that the spec is executable, then
returns a LOCKED copy. Execution is only permitted on locked versions (gate 19).

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
- `models.py` — SQLAlchemy 2 declarative rows: `Project`, `Strategy`,
  `StrategyVersionRow`, `RunRow`, `JobRow` (unique `idempotency_key`),
  `FailureRow`, `ArtifactIndexRow`, `EvidenceVerificationRow`, `DataSourceRow`.
- `database.py` — engine/session factory; `FENRIX_DATABASE_URL` (Postgres in
  prod, SQLite default for dev/tests); `session_scope` transactional context.
- `repositories.py` — the sanctioned read/write path exchanging domain objects.
  `JobRepository.submit` is idempotent (duplicate `idempotency_key` returns the
  existing job — duplicate-submission protection).
- `migrations/` — Alembic; initial migration creates all 9 tables. Round-trip
  (upgrade→downgrade→upgrade) proven in `tests/integration/test_migrations.py`.

## Artifact store (`app/evidence/`)
- `artifact_store.py` — `ArtifactStore` ABC + `Filesystem`/`InMemory` impls and
  `ArtifactRef`. See ADR-0006. Public serialization contains no filesystem path.

## Test coverage
- `tests/contract/` — StrategySpec hash + gating (25), run/job state machine
  (11), failure + versioning (10).
- `tests/evidence/` — artifact store (25).
- `tests/integration/` — persistence + repositories (6), migrations (3).
