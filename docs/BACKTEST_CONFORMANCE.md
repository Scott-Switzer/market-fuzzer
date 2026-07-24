# Backtest Conformance

This document records how the Fenrix submission portfolio engine
(`app/strategy_lab/submission/engine.py`) was validated against an independent
reference, per the submission-hardening requirement (§8).

## Engine conventions

The Fenrix engine is a **cross-sectional, next-open-fill** backtester:

| Concern | Convention |
|---|---|
| Signal timing | Features and target weights are computed **after close `t`**. |
| Execution | Orders fill at **open `t+1`** (plus any `execution_delay_days`). A signal produced on the last available row cannot execute. |
| Sizing | Target notionals = target weight × **pre-trade portfolio equity** (net liquidation value), not cash. |
| Weights | Equal-weight within the long and short sleeves, capped at `max_position_weight`. `short_quantile = 0` means a genuine long-only book (no forced short). |
| Gross/net | Feasible gross = (number of selected positions) × `max_position_weight`; if the declared gross target exceeds feasibility the book is scaled down and a warning is emitted. |
| Costs | Commission, half-spread per executed side, slippage, daily borrow accrual on outstanding short market value, and optional locate/entry fee. All labeled **heuristic**, not broker-calibrated. |
| Benchmark CAGR | Geometric: `(end / start) ** (252 / n) - 1`. |
| Self-financing | An explicit cash ledger ties equity every row: `equity[t] = cash[t] + Σ shares[t] · close[t]`. |

## Reference engine

Two independent references are used:

1. **Deterministic analytic reference (authoritative, dependency-free).**
   A 1-asset, pure long-only, zero-cost run must exactly track the price ratio:
   `equity[-1] / equity[0] == price[-1] / price[0]` (within 1e-6). This isolates
   the accounting and next-open fill convention with a closed-form answer.

2. **`pmorissette/bt` (MIT), the spec-preferred external reference.**
   A monthly-rebalanced, equal-weight, zero-cost strategy is run in `bt` on a
   fixed price panel and its final equity is cross-checked against the Fenrix
   engine within 3% tolerance. This test is **skipped gracefully** if the
   installed `bt` version exposes a different result API, so it never blocks CI,
   but it runs and passes in the pinned environment.

The test file is `tests/submission/test_conformance_bt.py`.

## Differences

- `bt` marks and rebalances on the **close** of the rebalance day; the Fenrix
  engine forms the signal at close `t` and **fills at the next open**. On the
  synthetic conformance panel `open ≈ close`, so the two agree within tolerance,
  but the one-row execution lag is a real and intentional difference (it is what
  removes the same-open lookahead documented in §3.1 of the hardening spec).
- The Fenrix engine holds cash until the first valid signal after the momentum
  lookback; a from-`t=1` reference is invested slightly earlier. This lookback
  head-start is expected and bounded.
- Costs: the conformance tests run with **all costs set to zero** so the
  comparison isolates accounting and timing. Cost accounting is validated
  separately in `tests/submission/test_portfolio_accounting.py`.

## Tolerance

| Check | Tolerance |
|---|---|
| 1-asset analytic buy-and-hold | 1e-6 (relative) |
| `bt` monthly-rebalanced equal-weight | 3% (relative, final equity) |

## Test result

```
tests/submission/test_conformance_bt.py::test_engine_matches_buy_and_hold_analytic  PASSED
tests/submission/test_conformance_bt.py::test_engine_matches_bt_secondary           PASSED
tests/submission/test_conformance_bt.py::test_engine_rejects_unsupported_cost_model PASSED
```

The engine reproduces the analytic buy-and-hold result to floating-point
precision and matches the `bt` monthly-rebalanced reference within tolerance,
confirming that the accounting, sizing, and next-open execution conventions are
implemented as documented.
