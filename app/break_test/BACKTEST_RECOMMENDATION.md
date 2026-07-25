# Backtesting Architecture Recommendation — `app/break_test`

## Recommendation: Custom hybrid wrapper over existing code, **not** a migration

Verdict: extend the existing in-repo `app/break_test/` surface into a curated dual-mode backtest harness. Do not rewrite around Zipline Reloaded, vectorbt, QSTrader, or LEAN for the current product scope.

## Why not imported frameworks

- **Zipline Reloaded**: event-driven, but discontinued/niche, pandas-heavy, no native multi-asset margin/settlement, and incompatible with this repo’s deterministic tick/kernel architecture.
- **vectorbt**: vectorized + nice plotting, but not true event-driven, no realistic execution bridge, and it would force a destructive data-model change.
- **QSTrader**: event-driven and realistic, but large, opinionated about feeds, cash-in-advance accounting, and not currently packaged in a way that wraps our existing exchange/trade/bridge stack.
- **LEAN (QuantConnect)**: production-class event-driven engine, but C#, licensed around QC cloud, and not embeddable as a Python library.

This repo already has a purpose-built event kernel (`EventKernelV2`), matching engine, and vectorized metrics. The gap is policy, not engine existence.

## Target file plan

| File / layer | Role |
|---|---|
| `app/break_test/service.py` | Existing public entrypoint; keep as dispatch surface |
| `app/break_test/multi_mode_backtest.py` **new** | Wrapper choosing vectorized fast path vs event bridge path |
| `app/break_test/portfolio_accounting.py` **new** | Per-asset mark-to-market, margin, dividend/tax, cash-settlement bookkeeping |
| `app/break_test/data_policy.py` **new** | point-in-time bus, corporate-action rules, survivorship disclosures |
| `app/break_test/metrics.py` | Extend: add mode flag, per-asset attribution, benchmark normalization |
| `app/break_test/execution_bridge.py` | Reuse as event-driven executor; add multi-symbol router |
| `app/break_test/exchange_fwd.py` | Reuse for synthetic forward; add true factor/generic simulation options |
| `app/api/quant/backtest.py` | Adapter if needed to expose `/api/quant/backtest` mode selector |

## Mode definitions

### A. Vectorized backtest mode
Use `app/break_test/metrics.backtest_metrics` and `compute_equity_curve` for fast research-grade runs on OHLCV arrays. Add a per-asset vectorized path that returns `(positions_per_asset, returns_per_asset, costs_per_asset)` and aggregates them with symbol-level correlation matrix.

### B. Event-driven backtest mode
Route through `DeterministicUserStrategyBridge` (`execution_bridge.py`). This preserves order submission, matching, queue position, partial fills, maker/taker fees, and latency, which vectorized mode cannot provide. For multi-asset strategies, extend the bridge to maintain one `OrderBook` per symbol per regime world.

## Point-in-time data rules

Add `app/break_test/data_policy.py` with three tiers:

1. **T-0 bars only (default)**: signals generated at `t-1` close, execute at `t` open/close session boundary. No intraday lookahead.
2. **Lag discipline**: `lookahead_bar_count=0`, strict `max(allowed_bars_ahead)` enforcement in strategy sandbox.
3. **Cache stamp**: serialize CSV dataset SHA-256 + load timestamp per session to `environment` block for auditability.

## Corporate-action / survivorship handling

This repo already has strong scaffolding; strengthen and standardize it:

- Use `load_yfinance_bulk(..., auto_adjust=True)` for research read-through.
- Emit explicit `corporate_actions` block in results with method, splits, dividends, and source.
- Hard `FAIL_CLOSED` for unknown corporate-action treatments on short-selling paths unless the user explicitly opts `raw_close`.
- Survorship bias: keep `_flag_survivorship_bias`, but make it a required disclosure on any multi-asset run. If >5% flat bars or no -10% days over 1000+ bars, surface `"survivorship_bias_flag": True` in `environment.limitations.input_warnings`.
- **MVP disclosure**: current repo uses synthetic forward paths for stress testing only; real backtests are either single-asset from yfinance or synthetic. Do not publish single-asset yfinance results as multi-asset portfolio performance without explicit dataset provenance.

## Execution timing

MVP rules:

- **Daily bar**: execute at `close` or next `open`. Default `close` for historical; allow `open` option in future.
- **Intrabar / intraday**: not supported in MVP; emit `NotImplementedError` with clear message that full order-book submissions belong in the event bridge.
- **Bar bounds**: `backtest_metrics` and `compute_positions` compute returns on `t-1` to `t`; cost accrual approaches should use `t` close for PnL reconciliation.

## Transaction costs

Reuse `app/break_test/costs.py` `TransactionCostModel` everywhere.

| Cost | Source | Notes |
|---|---|---|
| Spread | `_spread_bps(daily_vol)` | 2 bps default; scale with volatility |
| Taker/maker fee | `ExchangeSpec` | Harden all user paths to accept these |
| Almgren-Chriss impact | permanent + temporary | `perm_eta`, `temp_epsilon`, `temp_gamma` |
| Short borrow / HTB | borrow fees + locate failure | via `ExchangeSpec` HTB schedule |
| Toxicity | orderflow/depth | passthrough for event bridge; synthetic paths approximate |

Unify every backtest path through `TransactionCostModel.costs_for_signals(...)` so metrics and equity curves never diverge.

## Portfolio accounting

Add `app/break_test/portfolio_accounting.py` with:

- Per-asset `Position(symbol, quantity, avg_price, realized_pnl, unrealized_pnl, borrow_fee)`.
- `Portfolio.equity_t` series: cash + Σ market_value - fees - borrow_cost.
- Settlement lag: default T+2; synthetic path can vary per-Universe CSV column.
- Cash-in-advance check for shortable assets via HTB schedule + locate-failure probability.

## Benchmark support

- Relative to strategies: allow `benchmark_type` in request.
- Default: buy-and-hold of first asset in closes.
- Multi-asset: equal-weighted benchmark from universe prices.
- Metrics: `benchmark_total_return_pct`, `benchmark_sharpe`, `alpha`, `beta` (already exist in `backtest_metrics`).

## Metric definitions

Make these explicit in `app/break_test/metrics.py` docstring:

| Metric | Formula | Frequency |
|---|---|---|
| `total_return_pct` | `(equity[-1] - 1) * 100` | per run |
| `sharpe` | mean/std * sqrt(252) | annualized |
| `sortino` | mean / downside_std * sqrt(252) | annualized |
| `calmar` | `(end_equity - 1) / (-max_dd)` | annualized |
| `max_dd_duration_days` | longest straddle under water | calendar bars |
| `turnover` | Σ `abs(Δposition)` | per run |
| `var_95_pct` | 5th percentile daily return | daily |
| `cvar_95_pct` | mean below VaR threshold | daily |
| `win_rate_pct` | winning trades / total trades | trade-level |
| `profit_factor` | wins / |losses| | trade-level |
| `alpha` | mean(strategy_return - bench_return) | annualized rate |
| `beta` | cov(strategy_returns, bench_returns) / var(bench_returns) | volatility |
| `kelly_fraction` | mean/variance [0,1] | per run |
| `tas_by_bucket` | TCA bucketed by participation rate | order-level |
| `deflated_sharpe` | López de Prado 2014 | trial-adjusted |
| `pbo` | bootstrap probability of overfit | probabilistic |

## Testing strategy

Existing fixtures/extensions:

1. **Determinism tests**: Event bridge must reproduce identical order logs across reruns given manifest.
2. **Regression suite**: update `tests/`, and add:
   - `tests/break_test/test_multi_mode_backtest.py`
   - `tests/break_test/test_portfolio_accounting.py`
   - `tests/break_test/test_data_policy.py`
3. **Cross-mode parity**: for the same synthetic world, assert strategies’ equity paths aggregate to same Sharpe ± 1% between vectorized and event bridge.
4. **Cost invariance tests**: `TransactionCostModel.costs_for_signals` must equal `trade_cost_bps` loop-sum on more than 5 sampled paths.
5. **Data fixtures**: cache yfinance snapshots to JSON file fixtures to keep CI offline-friendly.

## MVP compromises / disclosures (must carry in results)

- **Single-asset by default today**: multi-asset support exists structurally but most public results currently exercise a primary synthetic asset path.
- **Synthetic stress != historical OOS**: synthetic forward regimes are scenario tools, not historical walk-forward out-of-sample evidence.
- **No live broker integration**: cash and margin rules are simulated.
- **No order-execution OCO / bracket orders** in MVP event bridge.
- **Corporate-action adjustment** is limited to yfinance auto-adjust and stock-split/dividend detection; no CEF spin-off tax treatment.
- **Survivorship bias** warning is surfaced for assets with < 252 bars, no large drops over long history, or >5% flat returns.
- **Fixed 2 bps fallback** removed from public user-facing paths in favor of `TransactionCostModel`; remaining legacy reference is flagged.
