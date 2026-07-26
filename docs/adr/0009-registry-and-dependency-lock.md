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

- `pip-tools` is a dev dependency. The lock is regenerated with one documented
  command (`make lock`): `piptools compile --extra dev --resolver backtracking
  --generate-hashes --strip-extras --allow-unsafe -o requirements.lock
  pyproject.toml`.
- `--allow-unsafe` is required so `pip`/`setuptools` are pinned; without it a
  `--require-hashes` install (which `--generate-hashes` implies) fails.
- `make lock-check` regenerates and runs `git diff --exit-code requirements.lock`;
  wired into CI as the `lockfile` job so pyproject can't change without re-locking.
- A fresh `python3.12 -m venv` installing `-r requirements.lock` then
  `--no-deps -e .` imports all declared deps (verified locally).

### Consequences

- The lock and pyproject describe the same graph, with hashes.
- `pip freeze` is explicitly **not** used for lock generation.
