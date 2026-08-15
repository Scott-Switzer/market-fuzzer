# ROUTE_AND_SURFACE_INVENTORY.md — Fenrix Product Reset v1, Phase 0

**Baseline:** `e0029ae6` on `reset/fenrix-product-reset-v1`.
Every route/surface below was extracted from source with `grep` at the baseline commit. Decisions are keep / adapt / quarantine / remove per §2 of the reset brief. **Nothing is removed in Phase 0** — decisions are recorded and executed in later phases (mostly Phase 8).

---

## 1. HTTP application topology

- Entry: `app/main.py` → `app/api/app.py` (**3,230 lines, 129 route decorators**).
- Static mount: `app.mount("/static", StaticFiles(directory=ROOT/"static"))` (app.py:121).
- Strategy-Lab router mounted at `/api/strategy-lab` (app.py:483) via `app/strategy_lab/service.py`, which composes:
  - `/strategies` (`api/strategies.py`)
  - `/backtests` (`api/backtests.py`)
  - `/campaigns` (`api/campaigns.py`)
  - `/reports` (`api/reports.py`)
  - `""` submission router (`api/submission.py`)
  - `""` optional `api_lab.py` (only when available)

---

## 2. Static page surfaces (9 HTML files) + serving routes

| Route (app.py) | File | Role | Keep/adapt/quarantine/remove |
|---|---|---|---|
| `GET /` (:477) | `break-test.html` | current landing (break-test) | **REMOVE** (replaced by React Projects landing, Phase 4 frontend). Keep temporary redirect. |
| `GET /strategy-lab` (:490) | `strategy-lab.html` | **real plain-English thesis entry** (`textarea#brief:90` → `/api/strategy-lab/compile`) | **ADAPT** — this is the flow to preserve/port to the React Strategy Builder (Phase 4 frontend). Its compiler machinery is reused. |
| `GET /submission` (:496) | `submission.html` | Fenrix MVP w/ "not executed" dropdowns; oversized single page | **QUARANTINE → REMOVE** after React replacement (Phase 4 frontend, Phase 8 cleanup). Keep redirect. Its `extra="forbid"` + hard-coded flagship is the core drift (P0-1). |
| `GET /legacy-start` (:502) / `GET /start` (:583) | `start.html` | abandoned onboarding | **REMOVE** (Phase 8), prove zero external links first |
| `GET /arena` (:508) | `arena.html` | legacy arena UI | **REMOVE** (Phase 8) |
| `GET /market-fuzzer` (:514) | `index.html` | legacy product page | **REMOVE** (Phase 8) |
| `GET /sealed-campaign` (:578) | `sealed-campaign.html` | legacy sealed-campaign UI | **REMOVE** (Phase 8) |
| `GET /break-test` (:829) | `break-test.html` | break-test UI (dup of `/`) | **ADAPT/REMOVE** — plain-English `plain_english` textarea also here; fold concept into React, then remove |
| `GET /synthetic-market-world` (:834) | `synthetic-market-world.html` | legacy | **REMOVE** (Phase 8) |
| `GET /strategy-stress-lab` (:840) | `stress-lab.html` | legacy "PLAIN-ENGLISH COMPILER" | **REMOVE** (Phase 8), concept folded into Failure Lab |

**7 zombie UIs** confirmed for removal (start, arena, index/market-fuzzer, sealed-campaign, synthetic-market-world, stress-lab; plus one of the two break-test routes). Removal is Phase 8 with proof of zero inbound links + redirect for any customer-facing path.

---

## 3. API route families in `app/api/app.py` (129 decorators)

Grouped by prefix (representative; full list via `grep -nE "^@app\.(get|post|put|delete)" app/api/app.py`):

| Prefix / family | Example routes | Backing module | Decision |
|---|---|---|---|
| Health/infra | `/api/health` (:529), `/api/ready` (:547), `/api/build-flags` (:520) | inline | **KEEP** (move to `routers/`) |
| Strategy-Lab (mounted) | `/api/strategy-lab/*` | `strategy_lab/service.py` | **KEEP/ADAPT** — product core |
| Submission pipeline | `/api/strategy-lab/submission/{compile,approve,backtest,stress,run}` (`api/submission.py:76–158`) | submission core | **ADAPT** — lift `extra="forbid"`, wire `description` (Phase 2) |
| `break-test` | `/api/break-test/{strategies,run,session/{id}}` (:738–796) | `app/break_test/` | **ADAPT** — plain-English concept; reconcile with canonical spec |
| `quant` | `/api/quant/{oos,sensitivity,worst-case}` (:689–810), `/api/robustness/sma` (:596) | inline/quant | **ASSESS** Phase 5 — fold valid stress into Tier-A; quarantine duplicates |
| `enterprise` (largest family, ~60 routes) | `/api/enterprise/{worlds,calibration,scenario-packs,regression-suites,strategies,sealed-campaigns,experiments,experiment-jobs}/…` (:845–1686+) | `evaluation/`, `campaigns/`, `persistence/` | **ASSESS/QUARANTINE** — legacy enterprise/arena stack. Large surface not part of the reset's core workflow; retain only what the validation/stress/evidence workflow needs, quarantine the rest behind ADR. |
| Artifact download | `/api/enterprise/experiment-jobs/{id}/artifacts/{kind}` (:1686), `artifact_download` (:2741 `FileResponse`) | inline | **ADAPT** — must route through `ArtifactStore`, never leak FS paths (P0-22) |

**Target (Phase 8):** split this god-file into `app/api/routers/{projects,strategies,compilation,validation,stress,exchange_replay,evidence,jobs}.py`. Preserve temporary redirects for any customer-facing legacy route; do not preserve broken internal architecture.

---

## 4. The plain-English regression — precise mechanism

- The real typing surface is **`strategy-lab.html:90`** (`<textarea id="brief">`) posting `{description: raw}` to `/api/strategy-lab/compile` (line 261) → `compiler/planner.py` → clause ledger. This works.
- The **public** `submission.html` never had a thesis textarea. Its `SubmissionRequest` (`api/submission.py:48`) is `ConfigDict(extra="forbid")` with **no `description` field**, so prose is rejected at the schema boundary; `orchestrator.build_strategy_dsl()` hard-codes the flagship L/S momentum strategy. Dropdowns (`universe-preset`, `template`) are labeled *"Example strategy goal — not executed."*
- **Fix path (Phase 2/3):** lift `extra="forbid"`, add & thread `description` into `build_strategy_dsl`, and make the React Strategy Builder lead with the thesis textarea reusing the already-working `/compile` machinery. This is a re-wire, not a rewrite of the compiler.

---

## 5. Frontend build status

No `web/` directory, no Vite, no React, no package.json for an app (only vendored JS in `app/static/*.js`, e.g. `submission.js`). All 9 surfaces are hand-written HTML + vanilla JS. **Phase 4 (frontend) creates `web/` as a real React/TypeScript/Vite/Salt application**; the HTML surfaces are retired with redirects as each React route reaches parity.
