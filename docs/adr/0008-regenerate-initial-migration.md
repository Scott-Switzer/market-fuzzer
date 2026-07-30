# ADR 0008: Regenerate the initial Alembic migration; add DB-level invariants

- Status: Accepted (Phase 1.1)
- Date: 2026-07-25

## Context

The Phase 1 initial migration (`807a7eb0ee63_initial_schema`) matched an
under-constrained schema:

- `runs.strategy_id` was a bare string with no foreign key.
- No composite FK linked a run to a real `(strategy_id, version)` row.
- `jobs` lacked check constraints on `attempts`, `progress`, `state`, `stage`.
- `artifact_index` lacked a uniqueness constraint on `(run_id, store, key)`.
- No metadata naming convention was configured, so Alembic could not emit stable
  `ALTER`/`DROP` names on PostgreSQL.
- `strategy_versions` stored `spec_json` (a re-serializable dict) rather than the
  immutable canonical JSON string introduced in ADR 0007.

The branch has **never been deployed** — it is an unmerged reset branch with no
production database. There is therefore no migration history to preserve for real
data.

## Decision

Because no environment has ever run the Phase 1 migration, we **regenerate the
initial migration** (`c77dbd35bd12_initial_schema_v1_1`) rather than stacking a
corrective second migration on top of a schema that never shipped. This keeps the
migration graph honest: one initial migration that produces exactly the current
models, verified by `alembic check` reporting no drift on both SQLite and
PostgreSQL.

The regenerated schema adds:

- A `MetaData(naming_convention=...)` covering pk/fk/uq/ck/ix.
- `runs → strategy_versions` **composite foreign key** on `(strategy_id, version)`.
- `jobs` check constraints: `attempts >= 0`, `max_attempts >= 1`,
  `attempts <= max_attempts`, `0 <= progress <= 1`, and `state`/`stage` value
  allow-lists derived from the domain enums (so they cannot drift).
- `artifact_index` `UNIQUE(run_id, store, artifact_key)`.
- `failures` indexes on `run_id`, `strategy_hash`, `mechanism`.
- `strategy_versions.canonical_json` (immutable approved source of truth).

## Alternatives considered

- **Second corrective migration** (the brief's default preference): correct when
  an initial migration has shipped. Rejected here *only* because nothing has ever
  applied the initial migration; regeneration yields a cleaner, reviewable graph.
  If this branch is ever deployed before merge, switch to the additive approach.

## Consequences

- `alembic check` is clean on SQLite and PostgreSQL (proven in CI `postgres` job
  and locally against a Docker Postgres 16).
- FK + check constraints are enforced on PostgreSQL (proven in
  `tests/integration/test_postgres_persistence.py`) and on SQLite with
  `PRAGMA foreign_keys=ON` (set in `app/persistence/database.py`).
- `alembic check` is wired into CI to catch future model/migration drift.

## Evidence-verification retention

`evidence_verification` keeps **one** record per run (`UNIQUE(run_id)`): the
latest verification result. Verification history is intentionally not retained in
v1.1; if an audit trail of re-verifications is needed later, add an append-only
`evidence_verification_history` table in a follow-up migration.
