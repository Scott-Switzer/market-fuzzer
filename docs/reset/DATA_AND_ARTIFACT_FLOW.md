# DATA_AND_ARTIFACT_FLOW.md — Fenrix Product Reset v1, Phase 0

**Baseline:** `e0029ae6` on `reset/fenrix-product-reset-v1`.
Traces how data enters, how run artifacts are produced/addressed/stored, and where fabricated or misleading artifacts live. Backed by reads of `submission/evidence.py`, `submission/cli.py`, `.gitignore`, and the `artifacts/`/`data/` trees.

---

## 1. Data tiers (three-tier, visible labels, no silent fallback)

| Tier | Source | Adapter | Calendar | Label / provenance | Notes |
|---|---|---|---|---|---|
| 1 | Fenrix anonymized bundle | `submission/fenrix_adapter.py` | **relative** (`DAY_0000…`, not calendar) | `DataProvenance(source, tier, label)`, flag `dates_are_relative` | `market/price_series.csv` = `date,price` (close only). `metrics/daily_prices.json` is CORRUPT/duplicated — do not trust. Financial ratios are fiscal-year, NOT point-in-time. Distributional template only; never expose real identities. |
| 2 | yfinance | `submission/yfinance_adapter.py` | real calendar | `data_mode=yfinance` | Cached, bounded-retry. **SPY fetched WITH the universe but EXCLUDED from tradable assets** (benchmark_close only). yfinance is undeclared in pyproject (see DEPENDENCY audit). |
| 3 | deterministic synthetic fixture | `submission/fixture.py` | synthetic | `data_mode=synthetic_fixture`, tier 3 | For CI/offline. Labeled explicitly — must never be presented as historical (§16-5). |

`SubmissionRequest.mode ∈ {auto, fenrix, yfinance, synthetic_fixture}`. `historical` is **invalid** (422). Every `MarketDataPanel` carries a `DataProvenance`; the UI/deck shows the tier. **Integrity rule (§16-5/21):** synthetic data must never be labeled historical; unsupported data must never be silently forward-filled.

---

## 2. Run → artifact pipeline (current)

`POST /api/strategy-lab/submission/run` → orchestrator → backtest → stress_search → `build_evidence_package(run, save_dir)` (`evidence.py:60`).

**Directory layout** (`evidence.py:62-64`): `artifacts/submission/<dir>/{data,strategy,historical,synthetic,replay,demo,pitch}/`

- Canonical `<dir>` is named by **strategy hash** via `_git_sha()` usage inside `build_evidence_package`; the manifest/deck carry the git SHA. (Confusingly, the fn calls it `sha` but it is used as the package dir key.)
- `cli.py:45-50`: yfinance → `artifacts/submission/<git_sha>`; synthetic → `<git_sha>-synthetic` (so synthetic never clobbers the yfinance run of record). `--allow-synthetic-current-sha` overrides for CI offline audit.

**Per-directory contents (verified from `evidence.py`):**

| Dir | Files | Real? |
|---|---|---|
| `strategy/` | `original_description.txt`, `clause_ledger.json`, `approved_strategy.json`, `strategy_hash.txt` | ⚠️ `original_description.txt` **hard-codes** "Fenrix Flagship Long/Short Momentum-Volatility strategy" (evidence.py:70-72) regardless of input — reinforces the drift (P0-1). `clause_ledger` now real (`run.clause_ledger`). |
| `historical/` | `metrics.json`, `equity_curve.csv`, `benchmark_curve.csv` (real SPY rebased), `weights.npy`, `trades.csv`, `exposures.csv`, `costs.json` | ✅ REAL run data |
| `data/` | `source_manifest.json` (data_mode, tier, universe, first/last dates, provenance) | ✅ REAL |
| `synthetic/` | Tier-A stress outputs, regime matrix | ✅ REAL (Tier-A) |
| `replay/` | `minimized_failure.json`, `adjacent_pass.json`, **`event_trace.jsonl`** | ⚠️ **`event_trace.jsonl` is FABRICATED** (evidence.py:173: first 200 backtest trades relabeled `{"type":"fill"}`). **P0 — quarantine Phase 6.** minimized/adjacent JSON are real Tier-A outputs. |
| `pitch/` | deck data, `equity_curve.png`, `CLAIMS_MANIFEST.json` | ✅ rendered from real run |
| `demo/` | demo bundle | ✅ |

**Manifest** (`evidence.py:~240`): per-artifact SHA-256, `replay_id`, artifact list including `replay/*`. **Gap (§16-17):** the manifest currently *includes* the fabricated `event_trace.jsonl` — a real exchange trace must replace it before the manifest can honestly claim replay coverage.

---

## 3. Storage & addressing gaps vs target (§11, §16-22)

| Current | Target | Gap |
|---|---|---|
| Artifacts written to raw `artifacts/submission/<hash>/…`; DB/manifest reference **string filesystem paths** | `ArtifactStore` interface (filesystem local / S3 prod); never leak FS paths to public UI | **Build `evidence/artifact_store.py`** (Phase 1). P0-22 risk: `artifact_download` (`app.py:2741`, `FileResponse`) and deck asset paths must go through the store. |
| `.gitignore`: `artifacts/*` (except README) — artifacts are **gitignored**, so a fresh CI checkout has none; audit fixture skips unless regenerated | Postgres artifact **index** + object store; CI regenerates deterministically | Confirmed: `.gitignore:14 artifacts/*`. Reproducibility must not depend on committed artifacts. |
| Metadata: none durable (no Postgres). Campaign `persistence/` is local | PostgreSQL authoritative for projects/strategies/versions/runs/jobs/failures/artifact-index/verification | Phase 1 build |
| Long jobs run in-process (FastAPI) | Celery + RabbitMQ workers, idempotency keys, state machine | Phase 1/11 (P0-23: work lost on restart) |
| Existing artifact dirs in tree: `artifacts/data_cache/yfinance`, `artifacts/cmv-*`, `artifacts/smw-*`, `artifacts/recovery` | object store namespaces | legacy local run outputs; not shipped (gitignored) |

---

## 4. Data provenance for Evidence / PROV (Phase 7 targets)

The evidence package must feed:
- **Evidence manifest** — schema version, project/strategy id+version, canonical strategy hash, data manifest hash, code source commit, per-engine versions, run params, seeds, artifact hashes, timestamps, status, limitations, verification result.
- **W3C PROV** — thesis/uploads = entities; compile/backtest/stress/replay/export = activities; approved strategy, minimized failure, strategy card, evidence package = generated entities; software versions + user approvals = agents. Invariant: no entity generated before its inputs existed.
- **OpenTelemetry trace** — request → compile → resolve → approve → acquire-data → historical-backtest → metrics → stress-campaign(world-evaluation) → minimize → adjacent-pass → exchange-replay → evidence-export → verify.

Current baseline has **none** of these (no OTel, no `prov`); artifact hashing + a manifest exist as the seed to build on.

---

## 5. Fabricated / hand-entered artifact audit (§12, §16-18)

| Item | Status | Action |
|---|---|---|
| `replay/event_trace.jsonl` | **FABRICATED** (relabeled backtest trades) | Quarantine; replace with real exchange events (Phase 6) |
| `strategy/original_description.txt` | **Hard-coded** flagship string, ignores user thesis | Fix when `description` is wired (Phase 2) |
| `evidence/exports.py _build_historical_content` | Fabricates fallback rows when curve missing | Retire legacy exporter (Phase 7/8) |
| Deck numbers | Rendered from real run (`deck_data.json`) — **not** hand-entered | Keep; verify no hand-entry in React port |
| Deck source-SHA | "Source code SHA" = generating commit (ancestor of HEAD) | Keep the strengthened audit invariant |

**Standing rule (§12):** no fabricated artifacts, fallback rows, inferred exchange fills, invented adjacent passes, or hand-entered deck figures. The single P0 fabrication (`event_trace.jsonl`) is the gate item for Phase 6.
