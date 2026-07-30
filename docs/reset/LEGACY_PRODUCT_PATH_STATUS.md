# LEGACY_PRODUCT_PATH_STATUS.md — Phase 2.5 cutover

This document records the legacy (pre-Phase-2.5) product routes that remain in
the codebase after the canonical product-path cutover. Per the reset brief, the
authoritative UI now uses ONLY `/api/strategy-lab/v2/*` and the four-surface
`strategy-lab.html` workspace. Legacy routes are quarantined: they may return
deprecation metadata but must not be invoked by the authoritative UI, and must
not execute different semantics behind the same response.

| Legacy route | Module | Current caller | Reachable? | Deprecation | Canonical replacement | Deletion prerequisite |
|---|---|---|---|---|---|---|
| `POST /api/strategy-lab/compile` | `api_lab.py:compile_strategy` | legacy `strategy-lab.html` (pre-cutover) | yes (mounted) | add `deprecated:true` + `replacement` | `POST /api/strategy-lab/v2/compile` | remove when no caller references it |
| `POST /api/strategy-lab/approve` | `api_lab.py:approve_strategy` | none (UI used compile only) | yes (mounted) | add `deprecated:true` + `replacement` | `POST /api/strategy-lab/v2/approve` | same |
| `POST /api/strategy-lab/backtests` | `api_lab.py:strategy_lab_backtest` | none (legacy `guarded_pov` path) | yes (mounted) | add `deprecated:true` + `replacement` | `POST /api/strategy-lab/v2/backtests` | same |
| `POST /api/strategy-lab/sealed/run` | `api_lab.py:strategy_lab_sealed_run` | none | yes (mounted) | add `deprecated:true` | `POST /api/strategy-lab/v2/campaigns` | same |
| `POST /api/strategy-lab/replay/minimize` | `api_lab.py:strategy_lab_minimize` | none | yes (mounted) | add `deprecated:true` | canonical campaign minimize (server-side) | same |
| `POST /api/strategy-lab/evidence/export` | `api_lab.py:strategy_lab_export` | none | yes (mounted) | add `deprecated:true` | `GET /api/strategy-lab/v2/audit` | same |
| `GET /strategy-lab` (old 6-step demo) | `app/static/strategy-lab.html` | n/a (static) | yes (served) | REPLACED by 4-surface UI in same file | same file | n/a |

## Honesty guarantees enforced by tests

- No `guarded_pov`, `arena_policy`, `deterministic_product_fixture`, or
  hard-coded `trend_reversal` failure objects remain in the authoritative UI or
  v2 router. (Scoped test in `tests/strategy_lab/test_canonical_honesty.py`.)
- Legacy `api_lab.py` `/compile` MUST NOT swallow new-compiler exceptions to fall
  back to `StrategyPlanner`. (The dual-compiler nesting is removed in favor of
  the single authoritative v2 compile; legacy `/compile` is marked deprecated.)
- Replay/minimization only opens from a persisted confirmed failure; no replay
  is fabricated when none exists.

## Deletion note

These modules are NOT deleted in Phase 2.5 (quarantine rule: zero-importer proof
+ zero-route-dependency proof required before removal). Legacy adapters
(`historical/engine.py`, `fenrix_adapter.py`, `upload_adapter.py`) remain as
reference/possible reuse but are not on the canonical path.
