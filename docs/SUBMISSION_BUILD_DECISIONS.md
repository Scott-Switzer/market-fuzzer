# Fenrix Submission MVP — Build & Decision Document

**Branch:** `feat/fenrix-submission-mvp` (stacked on `fix/strategy-lab-integration-and-verification` @ `10330d1`)
**Recovery PR #43:** preserved, unmodified.
**Flagship claim:** Fenrix turns a strategy description into a reviewed, immutable contract, backtests it on historical
market data, then searches sealed synthetic market conditions for reproducible ways the same strategy can fail.

---

## 1. Current-state truth (verified by reading the tree)

- `app/strategy_lab/historical/engine.py::run_historical_backtest` is a **single-vector façade**:
  - `_normalize_prices` does `px = px[0]` for 2-D input — discards every asset except the first.
  - `compute_positions(strategy_type, px, ...)` is called once on a 1-D price vector.
  - `_portfolio_accounting` maps time index `i` to `assets[i]` (label-chasing), not simultaneous per-asset holdings.
  - Costs are partly hard-coded (`0.0002` commission/slippage) and not deducted from equity (snapshots have
    `cash_before/cash_after = 0.0`).
  - Dates are stringified integer indices (`str(i)`).
- The **DSL / compiler / approval / hash** layer (`dsl.py`, `service_lab.py`, `api_lab.py`) is real and sound:
  - `Strategy` (pydantic frozen), `ledger_hash`, `ApprovalService.lock` → `canonical_hash` + `strategy_id`.
  - Approval is blocked for unresolved clauses. This is preserved as the single source of strategy identity.
- The **sealed campaign** lifecycle (`campaigns/campaign_engine.py`, `api/campaigns.py`) is real and sealed
  (hidden params never leave the API). Reused as-is.
- `EvidencePackager._build_historical_content` **fabricates fallback rows** when the real equity curve is missing
  (`equity_rows.append([str(idx), str(100.0 + idx)])`). This violates the honesty stop-conditions and is
  replaced by writing REAL backtest rows (see §9).
- The Fenrix adapter hard-codes `/Users/scottthomasswitzer/Documents/scott-brain/22_Fenrix/anonymized_bundle.zip`
  and only reads `price_series.csv`. Replaced by a configurable, inspectable adapter.
- Synthetic `factor_models.py` is a stub; `world_factory.py` + `campaign_engine.py` provide a working
  GBM-correlated world generator and failure search. Reused.

## 2. Chosen historical-engine architecture

A **real T×N multi-asset portfolio backtester** (`app/strategy_lab/submission/engine.py`):

```
MarketDataPanel (T×N OHLCV + benchmark)
  → feature engine (momentum_12_1, volatility_63d)
  → target-weight matrix (cross-sectional rank → long/short quantiles, equal weight, caps)
  → rebalance scheduler (monthly, at close t)
  → next-open order generation (fills at open t+1)
  → per-asset fills + transaction costs (commission/spread/slippage/borrow)
  → cash/shares/positions ledger (daily mark-to-market)
  → portfolio + benchmark returns → report + provenance
```

**Timing convention (documented & tested):** signals use data available at **close t**; orders generated after
close t; fills at **open t+1**. Returns are computed close(t)→close(t+1) on the executed position. No same-bar
signal/fill leakage. Fundamentals (Fenrix) may enter features only if `effective_at <= signal timestamp`; the
Fenrix fundamentals have only fiscal-year periods (no `effective_at`), so they are used as a **lagged research
approximation** with a disclosed reporting lag, never claimed point-in-time.

**Shapes:** prices `T×N`, target_weights `T×N`, shares `T×N`, positions `T×N`. Shape mismatches raise; arrays are
never resized to conceal errors.

**Accounting invariant (asserted every date):** `equity == cash + Σ(shares × mark_price)`. Costs reduce cash and
equity. Short proceeds are held as a liability (cash received, negative shares).

## 3. Open-source sources & license decisions (Subagent B)

| Project | License | Decision |
|---|---|---|
| quantstart/qstrader | MIT | Concept reference (alpha model, portfolio construction); **no copy** |
| pmorissette/bt | MIT | **Zero-cost conformance oracle** (dev-only); not a runtime dep |
| stefan-jansen/zipline-reloaded | Apache-2.0 | Concept reference (DataPortal, calendars); **no copy** |
| QuantConnect/Lean | Apache-2.0 | Concept reference only; **do NOT import** |
| ml4trading/pyfolio-reloaded | Apache-2.0 | Metric definitions reference; our metrics are implemented in-repo |
| ranaroussi/yfinance | Apache-2.0 | **Runtime dependency** (Tier 2 data) |
| polakowo/vectorbt | Commons-Clause | **AVOID** (copying prohibited) |
| kernc/backtesting.py | MIT | Concept reference; not imported |

No Commons-Clause or AGPL code is present in the repo. `bt` is used only in tests (conformance oracle) and pinned
in dev extras. Attribution for any borrowed algorithm is recorded in this doc and code comments.

## 4. Data tiers (Subagent C)

- **Tier 1 — Fenrix anonymized bundle** when available (configurable path). Prices only in `price_series.csv`
  (`date,price`); OHLCV available in `metrics/daily_prices.json`. Fundamentals exist but are **not point-in-time**
  (fiscal-year buckets) → used only as lagged approximation if at all; primarily we run Fenrix **prices** through the
  same engine and show the adapter + provenance.
- **Tier 2 — yfinance** research/demo acquisition (fixed universe, fixed dates, retries, cache, labels).
- **Tier 3 — deterministic generated fixture** for CI/offline (≥6 assets, OHLCV, benchmark, trend+reversal,
  missing-data, known hand-calculated trades). Always labeled **synthetic**.

The active tier is stamped into the run manifest and UI source badge. Never present generated prices as historical.

## 5. Flagship strategy (defensible defaults, NOT tuned to look good)

```yaml
universe: fixed_demo_universe (20–30 liquid U.S. equities + SPY benchmark)
signal_frequency: monthly
rebalance_frequency: monthly
features:
  - id: momentum_12_1
    formula: close[t-21]/close[t-252] - 1
  - id: volatility_63d
    formula: std(daily_returns[t-63:t]) * sqrt(252)
composite: {momentum_weight: 0.75, low_volatility_weight: 0.25}
portfolio:
  long_quantile: 0.20
  short_quantile: 0.20
  gross_exposure: 1.0
  net_exposure: 0.0
  weighting: equal
  max_position_weight: 0.10
execution:
  decision_time: close
  fill_time: next_open
  commission_bps: explicit
  spread_bps: explicit
  slippage_model: explicit_bounded_heuristic
benchmark: SPY
```
Dates fixed: **2018-01-01 → 2025-12-31**. Universe + dates persisted in run manifest.

## 6. Synthetic failure story

Locked strategy hash → sealed campaign. **Tier A (fast):** portfolio engine evaluates many GBM-correlated worlds
with mechanism-specific shocks (momentum reversal, vol expansion, correlation breakdown, spread/slippage/borrow
inflation, short unavailability, delayed rebalance, universe churn). **Tier B (replay):** worst confirmed failure
routed through the existing exchange/execution replay (orders, fills, partial fills, spread, latency, costs,
inventory, failed shorts). Failure predicate (e.g. Sharpe below threshold / drawdown above threshold / cost consumes
excessive gross return) declared before the run. Confirmation requires repeated seeds + deterministic replay +
valid data + no engine errors. Minimization via delta-debug; adjacent passing case recorded.

## 7. Deck claim boundary

Every number in `docs/pitch-deck/index.html` and the PDF comes from `artifacts/submission/<sha>/pitch/deck_data.json`,
generated from the real run. No stale test counts, no absolute competitor claims, no pricing slide, no unsupported
benchmark. Limitations slide is mandatory. The Fenrix factor strategy is presented only as a lagged research
approximation; the flagship historical demo is the price-only momentum/volatility strategy.

## 8. Rejected alternatives

- **Import LEAN/Zipline wholesale:** too heavy, AGPL-adjacent ecosystem risk, overkill. Rejected.
- **Patch the façade in-place:** cannot satisfy "results differ when non-first assets change" or T×N accounting.
  Rejected; built a new engine module instead.
- **Copy vectorbt:** Commons-Clause license. Rejected.
- **Claim point-in-time Fenrix fundamentals:** bundle has no availability timestamps. Rejected; lagged approximation
  only, disclosed.

## 9. P0 / P1 / P2 scope

- **P0 (must work):** normalized panel; real T×N engine; flagship strategy; yfinance Tier 2 + synthetic Tier 3;
  strategy-hash invariant across backtest/campaign/replay/export; ≥1 confirmed + minimized failure + adjacent pass +
  exchange replay; evidence package; pitch deck from evidence; browser E2E; `make verify`/`docker-smoke` green.
- **P1:** Fenrix configurable adapter + inspect CLI + inventory; cross-engine (bt) conformance tests; UI 7-step wizard.
- **P2:** full Fenrix fundamentals factor strategy (lagged); intraday; news/SEC pipeline doc (`UNSTRUCTURED_DATA_ROADMAP.md`).

## 10. Honesty guardrails (stop conditions honored)

- Engine never discards assets; accounting ties; costs deducted; historical & synthetic hashes match; yfinance
  failure visible (never silent); fallback labeled synthetic; Fenrix fields not guessed; hidden world data never
  leaks; demo < 5 min after cache; browser E2E passes; deck numbers from generated run; CI green; license review
  complete (this doc).
