# Fenrix Strategy Execution Architecture (Phase 2)

This document describes the real strategy-execution pipeline introduced in
Phase 2. It replaces the `spec: Any` flagship-only path with an explicit,
registry-backed, executor + generic-accounting architecture.

## The pipeline

```
StrategySpec (canonical contract)
      |
      v
registered StrategyExecutor      (app/strategies/executors/*)
      |   build_targets(spec, context) -> TargetPlan  (T x N target weights)
      v
generic accounting simulator     (app/strategies/accounting.py)
      |   next-open fills, cash, shares, costs, exposures, equity identity
      v
BacktestResult
```

`app/strategies/pipeline.py::run_strategy` ties these together and re-verifies
the strategy hash at the execution boundary (hard stop on drift).

### Separation of concerns (QuantConnect-framework-inspired)

- **universe / signal / portfolio construction / risk** live in executors.
- **execution / accounting** lives in the generic simulator.

Executors decide TARGET WEIGHTS at the close of each decision bar; they may NOT
touch cash, shares, fills, commissions, or borrow. The accounting engine turns
targets into next-open fills and does all money math once, for every strategy.
This is why "60/40" and "long/short momentum" run the same accounting path yet
produce completely different trades. (LEAN/Qlib are references only -- neither is
a dependency.)

## Executors (`app/strategies/executors/`)

| Type | File | Signals | Notes |
|------|------|---------|-------|
| `cross_sectional_factor` | cross_sectional_factor.py | momentum, realized_vol, composite | long/short, quantile selection, gross/net split |
| `long_only_ranking` | long_only_ranking.py | momentum | top_n / top_quantile, no shorts |
| `time_series_signal` | time_series_signal.py | SMA crossover | single asset, long or cash |
| `static_allocation` | static_allocation.py | none | explicit target weights (60/40) |
| `tactical_allocation` | tactical_allocation.py | 12m return + 200d trend | top-K + trend filter, explicit fallback |

### Signal formulas (exact, tested)

- **momentum_12_1**: `close[t-skip]/close[t-long] - 1` (default skip=21, long=252).
  No window shortening: insufficient history => NaN (ineligible).
- **realized_volatility**: `std(daily_returns[t-window+1..t], ddof=1) * sqrt(252)`
  (default window=63; window is inclusive of return t).
- **simple_moving_average**: trailing mean of `window` closes inclusive of t.
- **average_rank**: percentile rank in (0,1] with AVERAGE ranks for ties;
  order-independent.

### Cross-sectional exposure math (`app/strategies/constraints.py`)

For declared gross `G` and net `N` (requires `G >= |N|`):
`long_gross = (G+N)/2`, `short_gross = (G-N)/2`. Equal-weight within side, capped
at `max_position`. Infeasible requests emit an `exposure_shortfall` diagnostic
rather than silently over-allocating.

### Scheduling (`app/strategies/schedules.py`)

ONE scheduler for all executors. Decision at the CLOSE of the final valid bar of
the period (month/week) or the current close (daily); execution at the next valid
open (handled by accounting). The final decision may remain unexecuted. This is
NOT the legacy "first trading day of the month" mask.

## The registry (`app/strategies/registry.py`)

"Supported" means "has a registered executor." Registration is explicit (no
discovery/scanning). `StrategySpec` never imports the registry; callers pass
`registry.supported_types()` into the domain approval gate. Invariant asserted by
tests: `UI types == compiler types == registry.supported_types() == API types`.

## The compiler (`app/compiler/`)

`compile_thesis(text) -> CompilationResult` (deterministic, no LLM). It maps a
small, explicit grammar to the RIGHT strategy type and emits one clause-ledger
entry per interpreted clause. It NEVER:

- maps long-only momentum to a long/short family;
- converts unknown prose into a default family (returns `unsupported`);
- hard-codes an S&P 500 point-in-time universe (missing universe -> required
  resolution or a labeled assumption);
- collapses the whole thesis into one clause.

`apply_resolutions(result, {...})` applies user answers (universe/fallback) and
re-validates. Templates (`app/strategies/templates.py`) are factories for the
SAME `StrategySpec`; a template and its plain-English equivalent hash identically
when semantic fields match (intent-only fields like `name`/`original_thesis` are
excluded from the hash -- see ADR 0007/0010).

## Legacy parity (`app/compiler/legacy_adapter.py`)

`cross_sectional_to_spec(legacy)` adapts the legacy `CrossSectionalSpec` to a
canonical `StrategySpec` (one-way; the new executor does not depend on the legacy
type). Parity tests prove signal + selection agreement on a fixed panel;
documented differences (month-end scheduling, no lookback shortening,
inclusive-of-t vol window) are expected corrections, not regressions. The new
canonical hash is authoritative; the legacy hash is retained as metadata only.
