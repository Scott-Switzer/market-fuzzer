# ADR 0009: "Supported" means "registered executor"; reproducible dependency lock

- Status: Accepted (Phase 1.1)
- Date: 2026-07-25

## Part A — Registry-backed executor support

### Context

The Phase 1 domain treated every `StrategyType` except `UNSUPPORTED` as
executable. That let the model advertise strategy families (tactical allocation,
static allocation, ...) that had no runtime implementation.

### Decision

A strategy type is executable **iff** a concrete executor is registered for it.
The single source of truth is `app.strategies.registry.StrategyRegistry`:

- Registration is **explicit** — no entry-point discovery, no import scanning, no
  import-order-dependent decorators.
- `StrategySpec` (domain) does **not** import the registry. The dependency arrow
  is `strategies → domain`. Callers pass `registry.supported_types()` into
  `spec.is_executable(...)` / `spec.blocking_reasons(...)` and the approval
  service.
- `registry.validate_complete(advertised)` fails if any advertised type (enum,
  template, compiler output, API surface) lacks an executor.

Required invariant (asserted by tests):

```
UI-visible types == compiler-executable types == registry.supported_types() == API types
```

Until an executor is registered (Phase 2), a type is blocked at approval. This
makes `TACTICAL_ALLOCATION` etc. explicitly non-approvable until implemented.

## Part B — Reproducible dependency lock

### Context

`pyproject.toml` declared Alembic/SQLAlchemy/Matplotlib/yfinance but
`requirements.lock` contained none of them: a clean install from the lock did not
reproduce the branch.

### Decision

- `pip-tools` is declared in a dedicated `lock` extra (NOT in `dev`): it is
  lock-generation tooling and drags in unpinned build backends
  (`setuptools`/`pip`) that would break a `--require-hashes` install if compiled
  into the runtime graph. CI installs it standalone in the `lockfile` job.
- The lock is regenerated with one documented command (`make lock`): `piptools
  compile --extra dev --resolver backtracking --generate-hashes --strip-extras
  -o requirements.lock pyproject.toml`. No `--allow-unsafe`, so no floating
  build tools land in the lock and regeneration is deterministic across
  environments (local venv == CI).
- `make lock-check` runs `scripts/lock_consistency_check.py`, which verifies the
  committed lock satisfies every dependency specifier declared in pyproject
  (project deps + `dev` extra; the `lock` extra is excluded). CI's `lockfile` job
  additionally installs the committed hashed lock under `--require-hashes` and
  imports the key deps, proving reproducibility. This pair replaces a raw
  `pip-compile | git diff` gate, which is flaky: pip-tools resolves against live
  PyPI, so an unrelated upstream release between commit and CI time would fail a
  byte-for-byte diff even when the repo is unchanged.
- A fresh `python3.12 -m venv` installing `-r requirements.lock` then
  `--no-deps -e .` imports all declared deps (verified locally).

### Consequences

- The lock and pyproject describe the same graph, with hashes.
- `pip freeze` is explicitly **not** used for lock generation.
