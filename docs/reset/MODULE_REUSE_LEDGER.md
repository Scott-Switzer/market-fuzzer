# MODULE_REUSE_LEDGER.md — Fenrix Product Reset v1, Phase 0

**Baseline:** `e0029ae6` on `reset/fenrix-product-reset-v1`.
Module-by-module keep / adapt / quarantine / remove ledger. **Deletion rule:** no module is removed until (a) dotted-path importers proven zero, (b) zero route dependency, (c) zero required test coverage, each shown with command output. **Phase 0 removes nothing.**

Liveness method: `grep -rEn "strategy_lab\.<dotted>" app tests scripts` (exclude self + `__pycache__`) + mount check (`include_router` in `service.py`/`app.py`) — an *imported* router is not a *mounted* router.

---

## 1. ALIVE — product core (KEEP / ADAPT)

| Module | Importers / mount proof | Tests | Decision |
|---|---|---|---|
| `app/strategy_lab/submission/*` (11 files: strategy, engine, panels, orchestrator, stress_search, evidence, fixture, yfinance_adapter, fenrix_adapter, cli, deck) | CLI wired in Makefile + CI; `api/submission.py` mounts endpoints | `tests/submission/*` → **35 passed, 1 skipped** | **KEEP + ADAPT.** This is the reset core. Adaptations: wire `description` (lift `extra="forbid"`), consume canonical `StrategySpec` (Phase 2), stop fabricating replay (Phase 6). |
| `app/strategy_lab/api/submission.py` | mounted in `service.py:16` at `/api/strategy-lab` | `tests/strategy_lab/test_submission_run_endpoint.py` | **ADAPT** — add `description`; split per target router layout |
| `app/strategy_lab/api_lab.py` | mounted `service.py:20` (optional) | compile tests | **KEEP** — `/compile` clause-ledger machinery reused by React Strategy Builder |
| `app/strategy_lab/compiler/*` (planner.py etc.) | via `api_lab.py` → `/compile` | compiler tests | **KEEP/ADAPT** — becomes `compiler/{deterministic_parser,llm_parser,resolver,canonicalizer}` |
| `app/strategy_lab/historical/{engine,metrics,costs,data_contracts}.py` | `api/backtests.py:9`; `submission/evidence.py` | `tests/test_strategy_lab_backtest.py` | **KEEP + REPAIR** to §7 requirements (T×N, no same-bar leakage, exposure from contemporaneous equity, no double-counted costs) |
| `app/strategy_lab/historical/fenrix_adapter.py` | `historical/engine.py:16` | via engine tests | **KEEP** — *prior map wrongly called this DEAD; it is imported.* |
| `app/strategy_lab/historical/upload_adapter.py` | `historical/engine.py:17` | via engine tests | **KEEP** — *same correction.* |
| `app/strategy_lab/api/{strategies,backtests,campaigns,reports}.py` | mounted `service.py:12-15` | respective tests | **KEEP/ADAPT** into target router layout |
| `app/strategy_lab/{dsl,service_lab,_legacy,data}.py` | imported by app.py, strategy_language.py, external_adapter.py, sealed_campaign_service_v1.py | yes | **KEEP** short-term; migrate callers before any move |
| `app/strategy_lab/runtime/*` | via `api/strategies.py` | yes | **KEEP** |
| `app/strategy_lab/campaigns/campaign_engine.py` | via `api/campaigns.py` | yes | **KEEP/ASSESS** |
| `app/strategy_lab/synthetic/asset_anonymizer.py` | used by campaign_engine | yes | **KEEP** |
| `app/strategy_lab/persistence/` | campaign persistence | yes | **KEEP → migrate** to Postgres/SQLAlchemy (Phase 1) |

---

## 2. ALIVE — exchange engine (KEEP; wire Tier-B)

| Module | Importers | Tests | Decision |
|---|---|---|---|
| `app/exchange/*` (order_book.py, v2_matching.py, latency.py, volume_profile.py, volume_simulator.py, orders.py, market.py, v2*, __init__) — **2,356 LOC** | 10+ non-test modules: `simulation.py`, `calibration/`, `agents/{behaviors,user_strategy_agent}.py`, `orderflow/providers.py`, `evaluation/{sealed_v1,v2_runner}.py`, `break_test/{synthetic_market,execution_bridge,bridge_account}.py` | `test_exchange.py`, `test_v2_matching.py`, `test_execution_replay.py`, `test_exchange_realism.py`, `test_exchange_forward_execution.py` → **25 passed in 178s** | **KEEP.** Real matching engine. **BUILD** `exchange/replay_adapter.py` + `exchange/event_schema.py` (Phase 6) to feed genuine events into the submission pipeline. Do not fork ABIDES/Nautilus. |

---

## 3. FABRICATED / MISLEADING (QUARANTINE — P0)

| Location | Problem | Decision |
|---|---|---|
| `app/strategy_lab/submission/evidence.py:173` | `event_lines = [{"type":"fill", **t} for t in bt["trades"][:200]]` → `replay/event_trace.jsonl`. **Relabels backtest trades as exchange events** (§16-7 violation). | **QUARANTINE in Phase 6.** Replace with real `ExchangeReplayEngine` output. Add failing regression test first: "replay events must carry matching-engine sequence/queue fields and originate from `app/exchange`." Until replaced, mark artifact clearly non-exchange. |
| `app/strategy_lab/evidence/exports.py` (`_build_historical_content`) | Fabricates fallback rows when curve missing (known debt) | **QUARANTINE** — the new submission pipeline does not fabricate; retire this legacy exporter in Phase 7/8 |

---

## 4. ZOMBIE — imported but unreachable (QUARANTINE → remove Phase 8)

| Module | Status | Proof | Decision |
|---|---|---|---|
| `app/strategy_lab/api/robustness_endpoints.py` | imported in `api/__init__.py:1` but **never** `include_router`ed in `service.py` | `grep robustness app/strategy_lab/service.py` → none | **UNMOUNT/QUARANTINE.** Removing it strands the whole `robustness/` pkg (search, orchestrator, replay, failure_taxonomy, minimizer, suggestions, attribution) + `synthetic/world_factory.py`, reached only via it + tests (`test_adversarial_robustness_pipeline.py`, `test_attribution.py`). Migrate/retire tests first. |

---

## 5. DEAD — zero dotted-path importers (QUARANTINE → remove Phase 8 after CI proof)

Verified with dotted-path grep (self + `__init__` re-exports excluded; `__init__` confirmed to not re-export these):

| Module | Importers | Decision |
|---|---|---|
| `app/strategy_lab/api/adapter.py` | 0 | REMOVE (Phase 8, after full-suite green proves no dynamic import) |
| `app/strategy_lab/domain/canonicalize.py` | 0 | REMOVE — *note:* real canonicalization moves to new `app/domain/strategy_spec.py` |
| `app/strategy_lab/domain/strategy_schema.py` | 0 | REMOVE |
| `app/strategy_lab/synthetic/bank.py` | 0 | REMOVE |
| `app/strategy_lab/synthetic/factor_models.py` | 0 | REMOVE |
| `app/strategy_lab/synthetic/regimes.py` | 0 | REMOVE |

Before any Phase-8 deletion: (1) re-run zero-importer grep at that HEAD, (2) run full test suite, (3) grep for `importlib`/`__import__`/`getattr` dynamic loads of these names, (4) delete in a dedicated reviewable commit.

---

## 6. Legacy static/enterprise surfaces

See `ROUTE_AND_SURFACE_INVENTORY.md` §2–3. 7 zombie HTML UIs + the large `enterprise/*` API family (~60 routes in the 3,230-line `app/api/app.py`) are **ASSESS/QUARANTINE**: retain only what the reset workflow needs; the rest quarantined behind an ADR and removed with redirects in Phase 8.

---

## 7. External / cross-repo reuse candidates (§15) — to inventory before Phase 2

User-owned repos to inspect (record source repo, commit, path, license, tests, decision, target path, new tests before any port):
- `FenriXFinance/FenrixQuant` — candidate: structured `investment_thesis / intended_use / expected_failure_conditions / known_limitations` form fields for the canonical `StrategySpec`; evaluators; data contracts.
- `fenrix-synthetic-data` — synthetic prototypes; anonymized-bundle template (distributional only, never expose real identities — per standing directive).
- Prior sweep finding to re-verify: **no order-book / matching-engine / synthetic-exchange code exists in any sibling repo** → `app/exchange/` is the only real one → confirms KEEP-HOMEGROWN.

**Rule:** never copy external code until license + provenance + exact source commit are recorded here (§14). Prefer selective ports of domain concepts over repo forks.
