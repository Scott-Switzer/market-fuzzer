# ADR 0010: Real strategy execution — executors, generic accounting, deterministic compiler

- Status: Accepted (Phase 2)
- Date: 2026-07-25
- Builds on: ADR 0004 (canonical StrategySpec), ADR 0007 (identity/immutability),
  ADR 0009 (registry)

## Context

Phase 1/1.1 made the *contract* real (canonical `StrategySpec`, identity,
immutability, registry gate). But the product still:

- ran every strategy through a `spec: Any` flagship backtester that understood
  strategy-specific fields (`momentum_lookback`, `long_quantile`, ...);
- had a compiler that silently swapped families (long-only momentum ->
  long/short), defaulted unknown prose to `macro_gated_risk_off`, hard-coded an
  S&P 500 point-in-time universe, and ledgered the whole thesis as one clause;
- advertised strategy types with no executor behind them.

## Decision

1. **Executor + generic-accounting split.** Strategy logic lives in five
   registered executors that emit a `TargetPlan` (T×N target weights). A single
   generic accounting simulator turns targets into next-open fills, cash, shares,
   costs, exposures, and an equity curve — for every strategy. Executors never
   touch money math. (Architecture reference: QuantConnect Algorithm Framework;
   loose coupling: Qlib. Neither is a dependency.)

2. **Five real executors**: cross_sectional_factor, long_only_ranking,
   time_series_signal, static_allocation, tactical_allocation — each with exact,
   hand-tested formulas and one shared scheduler (month-end/week-end/daily
   decision, next-open fill).

3. **Deterministic compiler.** `compile_thesis` maps a small explicit grammar to
   the correct strategy type with a per-clause ledger. Unknown prose returns
   `unsupported` (execution blocked) — never a substitute family. Missing
   universe becomes a required resolution, never a hard-coded index. Templates
   are factories for the same `StrategySpec`.

4. **Intent fields excluded from the hash.** `name`, `original_thesis`, the
   clause ledger, and compiler trace are volatile: a template and its
   plain-English equivalent hash identically when executable fields match
   (extends ADR 0007's VOLATILE_KEYS). Approved snapshots store a *full* JSON
   (`StrategySpec.full_json`) for byte-exact reconstruction while the hash is
   computed over the volatile-excluded canonical form.

5. **Legacy flagship adapter + parity.** `cross_sectional_to_spec` bridges the
   legacy `CrossSectionalSpec` one-way; parity tests prove signal + selection
   agreement, with documented corrections (month-end scheduling, no lookback
   shortening, inclusive-of-t vol window). The new hash is authoritative; the
   legacy hash is metadata only.

## Consequences

- "60/40" produces 60/40 trades, not the flagship long/short model — proven by
  test.
- Adding a strategy type without an executor is impossible to approve (registry
  gate) and caught by a test asserting UI == compiler == registry == API types.
- The legacy `spec: Any` engine remains in `app/strategy_lab/submission` for the
  existing submission flow and as the parity reference; new work flows through
  `app/strategies`. Migrating the submission flow onto the generic engine is a
  follow-up (kept out of scope so parity stays demonstrable).

## Backtest boundary note

`app/strategies/accounting.run_accounting` receives `StrategySpec`-derived inputs
(a `TargetPlan`, price panel, capital, cost model) — never raw strategy feature
fields. Accounting logic is not duplicated across executors.
