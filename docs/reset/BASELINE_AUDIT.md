# BASELINE_AUDIT.md — Fenrix Product Reset v1, Phase 0

**Branch:** `reset/fenrix-product-reset-v1` (cut from `main`)
**Baseline commit (verified):** `e0029ae6d0578e415f814735330476220dde474c`
**Baseline subject:** `Fenrix final submission: merge hardening stack into main (#46)` (2026-07-25 10:26:28 -0500)
**Auditor:** principal-engineer reset, Phase 0 (audit & freeze — no product redesign performed yet)
**Method:** every finding below is backed by a command run against the working tree at the baseline commit. Claims from the prior audit are treated as *hypotheses* and each is confirmed or corrected against actual output.

---

## 0. Remote-state verification (done before any change)

```
$ git clone https://github.com/Scott-Switzer/market-fuzzer /tmp/mf-audit
$ git -C /tmp/mf-audit log -1 --format='%H %ci %s' origin/main
e0029ae6d0578e415f814735330476220dde474c 2026-07-25 10:26:28 -0500 Fenrix final submission: merge hardening stack into main (#46)
$ git -C /tmp/mf-audit cat-file -t e0029ae6d0578e415f814735330476220dde474c
commit
$ git -C /tmp/mf-audit branch -a --contains e0029ae6...
* main   remotes/origin/HEAD -> origin/main   remotes/origin/main
```

**Result:** the current remote `main` HEAD is *exactly* the expected release baseline `e0029ae6`. No divergence. The local development clone at `~/Documents/OAI_Build_Week` is on the same commit with a working `.venv312` (numpy 2.5.1 / pandas 3.0.3). Work proceeds on `reset/fenrix-product-reset-v1`; `main` is never touched.

Repository size at baseline: **461 tracked files** (314 `.py`, 83 `.md`, 11 `.html`, 11 `.json`).

---

## 1. Prior-audit claims — verified or corrected

| # | Prior-audit claim | Verdict | Evidence |
|---|---|---|---|
| 1 | Real plain-English entry point is `app/static/strategy-lab.html` (thesis textarea → `/api/strategy-lab/compile` → registration → backtests → sealed synthetic → minimization → evidence) | **CONFIRMED** | `strategy-lab.html:90` `<textarea id="brief" …>`; line 261 `POST /api/strategy-lab/compile {description:raw}` |
| 2 | Public submission surface bypasses that flow: preset dropdowns, hard-coded flagship L/S momentum, displays goals it does not execute, oversized single-page, hashes over decisions | **CONFIRMED** | `submission.html:99` `<select id="universe-preset">`, `:111` label `"Example strategy goal — not executed"`, `:112` `<select id="template">`; `api/submission.py:48` `model_config = ConfigDict(extra="forbid")` with **no `description` field** → prose is structurally rejected |
| 3 | `app/exchange/` is a real matching-engine foundation (order book, matching, latency, volume profiles, replay, tests) | **CONFIRMED** | `app/exchange/` = 2,356 LOC across order_book.py (14 KB), v2_matching.py (29 KB), latency.py, volume_profile.py, volume_simulator.py, orders.py, market.py. Tests pass: `tests/test_exchange.py tests/test_v2_matching.py tests/test_execution_replay.py` → **25 passed in 178s** |
| 4 | Tier-A synthetic panel stress exists but Tier-B exchange replay is not connected to the primary validation workflow | **CONFIRMED** | `grep -rn "app.exchange" app/strategy_lab/` → **zero hits**. The submission pipeline imports no exchange module. |
| 5 | A replay artifact is fabricated by relabeling backtest trades as exchange events | **CONFIRMED — exact line located** | `app/strategy_lab/submission/evidence.py:173`: `event_lines = [json.dumps({"type": "fill", **t}, …) for t in bt["trades"][:200]]` → written to `replay/event_trace.jsonl`. This is a relabel of the first 200 **backtest** trades, not matching-engine output. **QUARANTINE / P0.** |
| 6 | Several legacy HTML/JS surfaces and dead/disconnected backend modules | **CONFIRMED (with corrections — see §2)** | 9 static HTML pages; `api/robustness_endpoints.py` imported in `api/__init__.py` but **never** `include_router`ed in `service.py` (zombie) |
| 7 | Dependencies misdeclared/unused: `yfinance`, `matplotlib`, `scipy`, `scikit-learn` | **CONFIRMED** | `yfinance` only in `requirements-render.txt:17`, absent from `pyproject.toml [project].dependencies`; `matplotlib` imported at `deck.py:154` but undeclared anywhere; `scipy`/`scikit-learn` → **zero imports repo-wide** (`grep -rln "import scipy|from scipy|import sklearn|from sklearn" app tests scripts` empty). See DEPENDENCY_AND_LICENSE_AUDIT.md |

**Every prior-audit claim (1–7) is confirmed against the actual baseline.** Two dead-module sub-claims from the older internal map were found *wrong* and are corrected in §2.

---

## 2. Corrections to the prior internal dead-code map

The prior internal map listed `strategy_lab/historical/fenrix_adapter.py` and `strategy_lab/historical/upload_adapter.py` as **DEAD**. **This is false.**

```
$ grep -rEn "from app.strategy_lab.historical.fenrix_adapter|…upload_adapter" app
app/strategy_lab/historical/engine.py:16:from app.strategy_lab.historical.fenrix_adapter import FenrixHistoricalAdapter
app/strategy_lab/historical/engine.py:17:from app.strategy_lab.historical.upload_adapter import HistoricalCsvUploadAdapter
```

`engine.py` is alive (imported by `api/backtests.py:9` and `tests/test_strategy_lab_backtest.py`), so both adapters are **transitively alive**. → **KEEP.** This is why the reset rule "prove zero importers before deletion" exists; a filename/basename grep produced false positives.

Verified genuinely zero-importer modules (dotted-path check, excluding self and `__init__` re-exports which were confirmed empty):

| Module | Dotted-path importers | Decision |
|---|---|---|
| `strategy_lab/api/adapter.py` | 0 | QUARANTINE → remove in Phase 8 after CI proof |
| `strategy_lab/domain/canonicalize.py` | 0 | QUARANTINE |
| `strategy_lab/domain/strategy_schema.py` | 0 | QUARANTINE |
| `strategy_lab/synthetic/bank.py` | 0 | QUARANTINE |
| `strategy_lab/synthetic/factor_models.py` | 0 | QUARANTINE |
| `strategy_lab/synthetic/regimes.py` | 0 | QUARANTINE |

`api/robustness_endpoints.py` — imported by `api/__init__.py` but **never mounted** (`grep robustness app/strategy_lab/service.py` → none). Removing it unlocks the whole `robustness/` package + `synthetic/world_factory.py` (reached only via it and tests). **Decision: unmount/quarantine in Phase 8, not Phase 0.** Deletion deferred until tests are migrated.

**No module is deleted in Phase 0.** All are recorded and left in place under freeze.

---

## 3. God-file & structure findings

- `app/api/app.py` = **3,230 lines**, **129 route decorators** — the oversized single application the target architecture splits into routers (`app/api/routers/*`). Contains legacy arena / execution / sealed-campaign endpoints plus the strategy-lab mount at `/api/strategy-lab` (line 483) and 9 static-page routes.
- `app/static/` = 9 hand-written HTML surfaces (see ROUTE_AND_SURFACE_INVENTORY.md), no build step, no `web/` React app.
- No `app/domain/` canonical `StrategySpec`, no `app/persistence/` SQLAlchemy/Postgres, no `app/jobs/` Celery, no `ArtifactStore` interface — all are Phase-1+ build items. Artifacts are written to raw `artifacts/submission/<hash>/` dirs referenced by string paths (see DATA_AND_ARTIFACT_FLOW.md).

---

## 4. Integrity gate status at baseline (§16 release-blockers)

| Gate (§16) | Status at baseline | Evidence |
|---|---|---|
| 1. Option displayed but not executed | **VIOLATED** | `submission.html` template/universe dropdowns are labeled "not executed"; flagship L/S momentum always runs |
| 5/7. Replay trace not from exchange events | **VIOLATED (P0)** | `evidence.py:173` relabels backtest trades |
| 6. Failure count vs visible rows | To re-verify in Phase 5 | not yet audited row-by-row this phase |
| 19. Different stages use different strategy hashes | **HELD** | strategy-hash invariant tested in `tests/submission/test_strategy_identity.py` (35 passed) |
| 22. Local FS paths in public responses | **AT RISK** | artifacts addressed by raw `artifacts/submission/<sha>/…` paths; no `ArtifactStore` indirection yet |
| 23. Long work lost on API restart | **VIOLATED** | no Celery; long jobs run in-process |

These become the tracked P0/P1 backlog carried into Phases 1–8.

---

## 5. Test & tooling baseline (freeze reference)

- Env prefix required (documented): `env -u PYTHONPATH PYTHONNOUSERSITE=1 PATH=".venv312/bin:…" .venv312/bin/python`. Plain shell python imports a wrong numpy.
- `tests/submission` → **35 passed, 1 skipped in 18.65s** (offline).
- Exchange engine tests → **25 passed in 178s**.
- 89 `test_*.py` files under `tests/`.
- CI (`.github/workflows/ci.yml`): installs `.[dev]`, playwright chromium, regenerates synthetic evidence + deck, `node --check`, `make verify`, `make verify-submission`, `AUDIT_FINAL=1` audit. Docker job runs `make docker-smoke`.
- LICENSE = MIT (© 2026 Scott Switzer).

**Freeze action taken:** regression coverage already exists around the compiler, strategy-hash invariant, and exchange engine (all green). The fabricated replay (`evidence.py:173`) is flagged for quarantine in Phase 6; a failing regression test asserting "replay events must originate from the exchange engine" will be added when Tier-B is built.

---

## 6. Phase-0 conclusion

The baseline is exactly as expected and the prior audit's seven claims are all confirmed (two stale dead-module sub-claims corrected). No destructive change performed. The reset can proceed to Phase 1 (domain contracts & persistence) on `reset/fenrix-product-reset-v1`. Companion Phase-0 documents:

- `ROUTE_AND_SURFACE_INVENTORY.md` — every route & static surface, keep/adapt/quarantine/remove.
- `MODULE_REUSE_LEDGER.md` — module-by-module reuse decisions with importer proof.
- `DEPENDENCY_AND_LICENSE_AUDIT.md` — declared vs used vs licensed.
- `DATA_AND_ARTIFACT_FLOW.md` — data tiers, artifact paths, fabricated-artifact locations.
