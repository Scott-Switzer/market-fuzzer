# DEPENDENCY_AND_LICENSE_AUDIT.md — Fenrix Product Reset v1, Phase 0

**Baseline:** `e0029ae6` on `reset/fenrix-product-reset-v1`.
Declared-vs-used-vs-licensed audit. Findings backed by `pyproject.toml`, `requirements*.txt`, `pip list`, and repo-wide `grep` for imports. **No dependency change is made in Phase 0** — this is the record; fixes land in Phase 2 (declaration hygiene) and Phase 8 (license gates).

---

## 1. Declared runtime dependencies (`pyproject.toml [project].dependencies`)

| Package | Constraint | Installed | Imported? | License (SPDX) | Policy (§14) |
|---|---|---|---|---|---|
| fastapi | `>=0.115,<1` | 0.139.2 | yes | MIT | ✅ auto-allow |
| httpx | `>=0.27,<1` | 0.28.1 | yes | BSD-3-Clause | ✅ |
| numpy | `>=2,<3` | 2.5.1 | yes | BSD-3-Clause | ✅ |
| openai | `>=2.0,<3` | 2.46.0 | yes (optional LLM compiler) | Apache-2.0 | ✅ |
| pandas | `>=2.2,<4` | 3.0.3 | yes | BSD-3-Clause | ✅ |
| pyarrow | `>=18,<25` | 24.0.0 | yes (Parquet) | Apache-2.0 | ✅ |
| pydantic | `>=2.10,<3` | 2.13.4 | yes | MIT | ✅ |
| pyyaml | `>=6,<7` | 6.0.3 | yes | MIT | ✅ |
| typer | `>=0.15,<1` | 0.26.8 | yes (CLI) | MIT | ✅ |
| uvicorn | `>=0.34,<1` | 0.51.0 | yes | BSD-3-Clause | ✅ |

Dev extras (`[project.optional-dependencies].dev`): hypothesis (MPL-2.0 — *manual review* per §14, but standard/dev-only), mypy (MIT), playwright (Apache-2.0), pytest (MIT), ruff (MIT). All acceptable; MPL-2.0 for hypothesis is dev-only and file-level copyleft — record ADR note but no product linkage.

---

## 2. MISDECLARED / UNDECLARED (the §2.7 findings — CONFIRMED)

| Package | Installed | Used at | Declared in pyproject? | Impact | Fix (phase) |
|---|---|---|---|---|---|
| **yfinance** | 1.5.1 | Tier-2 data adapter (`submission/yfinance_adapter.py`) | ❌ NO (only `requirements-render.txt:17`) | Clean `pip install -e .` silently degrades Tier-2 → "yfinance unavailable" at runtime | **Add to `[project].dependencies`** (Phase 2). License: Apache-2.0 ✅ |
| **matplotlib** | 3.11.1 | `submission/deck.py:154` (`import matplotlib; matplotlib.use("Agg")`) | ❌ NO (undeclared anywhere) | Deck/chart build breaks on clean install | **Declare** (Phase 2). License: matplotlib/PSF-style (BSD-compatible) ✅ |

---

## 3. INSTALLED BUT UNUSED (dead weight — CONFIRMED)

```
$ grep -rln "import scipy|from scipy|import sklearn|from sklearn" app tests scripts | grep -v __pycache__
(empty)
```

| Package | Installed | Imports repo-wide | Decision |
|---|---|---|---|
| **scipy** | 1.18.0 | **0** | **REMOVE** from env / do not declare. If a future metric needs it, declare explicitly with test. License BSD-3 (would be allowed) but unused = attack surface + install bloat. |
| **scikit-learn** | 1.9.0 | **0** | **REMOVE** from env / do not declare. License BSD-3. Unused. |

These are not in `pyproject.toml` — they entered the venv ad hoc. Action: ensure they are absent from any lockfile/requirements the product ships; they must not appear in the SBOM as product deps.

---

## 4. Version/config mismatches

| Issue | Location | Fix |
|---|---|---|
| `requires-python = ">=3.12"` but `[tool.ruff] target-version = "py311"` | `pyproject.toml` | Align ruff to `py312` (Phase 2) |
| CI/local ruff drift: lockfile pins `ruff==0.15.21`, installed `0.16.0`, CI uses 0.16 | `requirements.lock` vs env vs CI | Pin one version everywhere; ruff 0.16 reformats markdown code blocks (docs) — must match CI |
| `requirements.lock` lists `scipy`/`sklearn`? | verify | ensure lock excludes unused packages |
| Project name `synthetic-market-world` / description "Counterfactual synthetic exchange…" | `pyproject.toml:6-8` | rename to `fenrix` + product-accurate description (Phase 8) |

---

## 5. External-engine & OSS-core policy (§3) — recorded decisions

**Prohibited product dependencies (do NOT copy/vendor/fork/build-around):**
- **VectorBT** — Apache-2.0 **+ Commons Clause** → forbids selling/hosting-for-fee whose value derives from it. ❌
- **PyBroker** — Apache-2.0 **+ Commons Clause**. ❌
- **OpenBB** — **AGPLv3** (network copyleft). Study for inspiration only; never link/vendor/embed. ❌
- **Backtesting.py** / any AGPL — ❌ without explicit ADR + legal approval.

**Reference-only (do NOT fork into the product this reset):**
- **ABIDES** (jpmorganchase/abides-jpmc-public, archived) — architecture/latency/agent/scenario reference only. Future integration must be optional, isolated, pinned, independently tested. ADR-10.
- **NautilusTrader** — LGPLv3, large, live-execution scope not needed. Reference for deterministic event contracts + research/live parity; future adapter candidate. ADR-10.
- **QuantConnect LEAN** — Apache-2.0, commercially usable but too large for reset. ADR for future conformance adapter. Do not replace existing engine.
- **Microsoft Qlib** — MIT, commercially usable. Consider only later for point-in-time features / factor datasets. Not added until Fenrix's own spec/engine/UI are stable.

**Rationale confirmed:** `app/exchange/` (2,356 LOC, 25 tests passing) already provides a tested matching engine, so no external execution engine needs forking. **Decision: KEEP HOMEGROWN cores, adopt only standards** (OpenTelemetry Apache-2.0, W3C PROV via `prov` MIT, model-card format) — documented as ADR-10/ADR-11.

---

## 6. Approved product dependencies to ADD (§3.3, later phases)

- **Backend:** Pydantic v2 (present), SQLAlchemy 2 + Alembic (Phase 1), PostgreSQL driver (Phase 1), Celery 5.6 + RabbitMQ (Phase 1/11), DuckDB (test fixtures), OpenTelemetry SDK (Phase 7), `prov` (Phase 7). All MIT/BSD/Apache → auto-allow.
- **Frontend (Phase 3, in `web/`):** React+TS, Vite, `@salt-ds/*` (Apache-2.0), TanStack Query/Table (MIT), a router (React Router **or** TanStack Router — ADR to choose one), Plotly.js (MIT), React Hook Form + Zod (MIT), Playwright/Vitest/Storybook/`@axe-core/playwright` (all MIT/Apache).

---

## 7. Security/supply-chain gaps at baseline (§14) — backlog

| Control | Baseline status | Phase |
|---|---|---|
| `pip-audit` | ❌ absent | 8 |
| npm audit / lockfile scan | ❌ (no web/) | 8 |
| `pip-licenses` + JS license report | ❌ | 8 |
| CodeQL | ❌ | 8 |
| Dependabot/Renovate | ❌ | 8 |
| Secret scanning | ❌ | 8 |
| OpenSSF Scorecard | ❌ | 8 |
| CycloneDX SBOM (py + js) | ❌ | 8 |
| **GitHub Actions pinned by commit SHA** | ❌ — all pinned by **tag** (`actions/checkout@v4`, `setup-python@v5`, `docker/*@v3..v6`) | 8 — repin by SHA |
| Read-only default workflow permissions | ❓ verify (no `permissions:` block seen) | 8 |
| `SECURITY.md` / `THIRD_PARTY_NOTICES.md` | `THIRD_PARTY_NOTICES.md` exists (2.1 KB); `SECURITY.md` ❌ | 8/13 |

---

## 8. Dependency allowlist (to codify in `docs/security/DEPENDENCY_POLICY.md`, ADR-11)

- **Auto-allow after scan:** MIT, BSD-2/3-Clause, Apache-2.0, ISC.
- **Manual review:** LGPL, MPL, EPL, custom/dual/source-available (hypothesis MPL-2.0 falls here — dev-only, approved).
- **Rejected by default:** AGPL, Commons Clause, SSPL, non-commercial, field-of-use, no-derivatives, unknown/missing. (Catches VectorBT/PyBroker Commons Clause and OpenBB AGPL automatically.)
