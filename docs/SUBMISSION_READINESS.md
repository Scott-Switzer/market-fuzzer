# Submission readiness (judges)

Honest checklist for the sealed execution stress-test sprint. Verified against disk and the focused test suite below.

## Exact run commands

### App start (Strategy Break Test + Arena)

```bash
python3 -m venv .venv312
.venv312/bin/pip install -e '.[dev]'
ARENA_DEMO_AUTH=1 \
ARENA_DEMO_INSTRUCTOR_CODE=change-this-local-demo-code \
.venv312/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- Primary UI: <http://127.0.0.1:8000/> and <http://127.0.0.1:8000/break-test>
- Arena teaching demo: <http://127.0.0.1:8000/arena>
- Isolated judge path: `make judge-demo`

### Clean CLI demo (plain-English summary)

Verified end-to-end:

```bash
env -u PYTHONPATH PYTHONNOUSERSITE=1 .venv312/bin/python run_demo_clean.py
```

`run_demo_clean.py` clears polluted `PYTHONPATH` / editable finders, then runs `demo.py`. Expect historical load (or cached fallback), regime/signal line, forward-test regime table, **Failure summary** + **Alternatives**, and **Demo wall time**.

Equivalent (if your environment is already clean):

```bash
env -u PYTHONPATH PYTHONNOUSERSITE=1 .venv312/bin/python demo.py
```

## Exact test commands

### Focused sprint suite (verified: 75 passed, 0 failed)

```bash
env -u PYTHONPATH PYTHONNOUSERSITE=1 .venv312/bin/python -m pytest \
  tests/test_quant_oos.py \
  tests/test_oos_validation.py \
  tests/test_break_test.py \
  tests/test_edge_cases.py \
  tests/test_expanded_universe.py \
  tests/test_sprint_hours_0_4.py \
  tests/test_transaction_costs.py \
  tests/test_exchange_forward_execution.py
```

### Broader verify / Arena browser E2E

```bash
make verify
# or explicitly:
.venv312/bin/python scripts/browser_e2e.py
```

Break-test UI smoke screenshots (ad-hoc Playwright helpers under `tests/`):

```bash
# Server must already be listening (e.g. port 8000 or 8001 as the script expects).
.venv312/bin/python tests/browser_e2e.py
```

Screenshots land in:

```text
tests/browser_screenshots/
```

Existing captures include `01_page_loaded.png`, `02_after_run.png`, `03_results_rendered.png`, `04_quant_oos_check.png`. The canonical Arena lifecycle E2E (`scripts/browser_e2e.py`) asserts API/cookie behavior and does not write those PNGs; use the `tests/browser_e2e*.py` helpers when you need visual artifacts.

## Key modules (where to look)

| Area | Path |
|------|------|
| Simulation default V2 exchange + timeline/agent collection | `app/simulation.py` |
| Forward stress + optional process-pool workers | `app/break_test/exchange_fwd.py` |
| Cost / TCA models (toxicity, HTB/locate, borrow) | `app/break_test/costs.py`, `app/break_test/cost_model.py`, metrics in `app/break_test/metrics.py` |
| Execution Challenge Arena domain | `app/execution_arena.py` |
| HTTP `/arena` + API | `app/api/app.py` (`GET /arena`) |
| V2 event-kernel on V1 CLOB matching | `app/exchange/v2_compat.py` (`ExchangeEngineV2`) |
| Separate MatchingExchangeV2 (sealed eval runner path) | `app/exchange/v2_matching.py`, `app/evaluation/v2_runner.py` |

## Claim boundary (verified vs not claimed)

**Verified in this sprint**

- Forward / simulation path defaults to sealed **V2 = EventKernelV2 provenance ledger on the existing V1 CLOB matching surface** (`ExchangeEngineV2` / `DEFAULT_EXCHANGE_ENGINE = "v2"`). Audit digests and command/event contract for sealed runs — not a full rewrite of agent ecology.
- Transaction-cost / TCA-style fields (e.g. Almgren–Chriss / toxicity / borrow-related costs) are wired into break-test metrics and forward stress payloads where those code paths run.
- `run_exchange_forward_test(..., workers=N)` can use `ProcessPoolExecutor` with **deterministic seed partitioning**; `workers=1` and `workers=N` are intended to evaluate the same seed set. Default remains single-process (`workers=1`).
- `app/execution_arena.py` exists at repo root of the package; `/arena` serves the teaching Arena UI.
- Focused pytest set above: **75 passed**. Browser E2E for Arena lifecycle via `scripts/browser_e2e.py` (and ad-hoc break-test screenshot scripts) were verified green in the pre-submission audit.

**Not claimed / do not oversell**

- **Not** “full MatchingExchangeV2 agent-loop drop-in.” `MatchingExchangeV2` is a separate matching/account model used on sealed evaluation runner paths; the default simulation still uses V1 books + V2 kernel via `ExchangeEngineV2`. See `app/exchange/v2_compat.py`.
- Process-pool workers are **optional**; most demos and UI paths use `workers=1`. Parallelism is a throughput option, not a required product mode.
- Worlds, agents, order books, and hidden challenge fixtures are **synthetic / educational**. No claim of licensed black-box market-data feeds, venue-certified replay, or production capacity modeling. yfinance (or local CSV fallback) is used for historical closes in demos; that is not a market-data product license.
- Scores, ranks, and failure summaries are **challenge- or demo-relative**. They are not alpha proofs, Sharpe forecasts, best-execution quotes, or live-trading safety certification.
- Demo auth (`ARENA_DEMO_AUTH=1`) is scoped local teaching auth — not institutional SSO / production multi-tenant IAM.
- See also [`docs/LIMITATIONS.md`](LIMITATIONS.md) for the standing product claim boundary.

## ICP (one line)

Lean prop-shop quants and quant-tech leads who need a local, sealed stress gate on Python strategies without buying a market-data license — see README product / audience section.
