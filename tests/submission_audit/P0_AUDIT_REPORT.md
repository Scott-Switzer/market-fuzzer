# P0 Portfolio-Accounting Audit — Red-Team Report (Subagent A)

Engine audited: `app/strategy_lab/submission/engine.py` @ commit `dadce86`.
Tests: `tests/submission_audit/test_p0_*.py` (assert CORRECT behavior; failures = proof).
**All 7 tests FAIL against HEAD.** The working tree has an uncommitted engine fix in
flight; against it, P0-1, P0-3, P0-6 still fail (P0-6 via an off-by-one, see below).

Run: `env -u PYTHONPATH .venv312/bin/python -m pytest tests/submission_audit/test_p0_*.py -v`

---

## P0-1 Same-open lookahead — `test_fill_occurs_next_open_not_same_open`
Loop uses `active_target[t]` (the close-t decision: vol feature consumes returns
through close[t], row timestamped close[t]) and fills at `open_[t]` — the same
bar's open, hours before the decision exists.
**Hand calc:** lookbacks 10/2/5, daily calendar from 2022-01-01 ⇒ first live
target row t0=31. Correct: shares[31]==shares[30]==0, first holdings at t=32.
Engine: shares[31] already nonzero.
**Fix:** fill decision from t−1 at open[t]:
`target_shares = _weights_to_shares(active_target[t-1], open_[t], equity[t-1], close[t-1])`
(shift the rebalance decision row by one bar; day-0 must hold no position).

## P0-2 Sizing off cash, not equity — `test_second_rebalance_sizes_off_equity_not_cash`
Line ~350: `notional = weights * cash`. Weights are fractions of capital.
**Hand calc:** cap $1M, weights [0.5,0.5] net-long. Rebalance #1 (t=31): buy
$500k each ⇒ cash≈0, equity≈$1M. Rebalance #2 (t=59): correct notional =
0.5×equity ≈ $500k+PnL each; engine: 0.5×cash ≈ $0 ⇒ silently liquidates the
whole book (invested fraction ~0% vs required ≥90%).
**Fix:** `_weights_to_shares(weights, price, equity, mark)` with
`equity = cash[t-1] + Σ prev_shares·close[t-1]` at the call site.

## P0-3 Infeasible exposure silently underfilled — `test_default_exposure_config_feasible_or_rejected`
Defaults gross=1.0, quantiles=0.20, max_position=0.10 on N=7:
**Hand calc:** n_long=n_short=max(1,round(0.2·7))=1; per-side weight
(1.0/2)/1=0.50 → clipped to 0.10 ⇒ realized gross = 0.20 vs configured 1.00
(80% underfill, no warning). All metrics computed on a 5× smaller book.
**Fix:** in `cross_sectional_target_weights`, when
`n_side * max_position < gross_exposure/2` either
`raise ValueError("infeasible exposure config: ...")` or widen
`n_side = ceil((gross/2)/max_position)` and record a provenance warning.

## P0-4 Spread never charged — `test_spread_is_charged`, `test_spread_reduces_equity_vs_zero_spread`
`_charge_costs` deducts commission+slippage+borrow only; `spec.spread_bps`
(default 2.0) is dead config.
**Hand calc:** spread_bps=10, other costs 0; first rebalance trades ≈$1M
notional ⇒ expected ≥ $500 (half-spread), engine reports cost_total = $0 and
identical equity to a zero-cost run.
**Fix:** per trade `spread = spec.spread_bps/10_000 * notional / 2`; deduct
from cash, accumulate `spread_total`, add `"spread"` key to `cost_summary`
and include in `"total"`.

## P0-5 Borrow only at short open, not daily — `test_borrow_accrues_daily_on_held_short`
Line ~380: one day of borrow (`notional/252`) charged only when `qty < 0`
(which also mis-fires on long *sells*).
**Hand calc:** borrow_bps=365, short ≈$500k held ~38 bars. Correct accrual
≈ 365/1e4·500k/252·38 ≈ **$2,750**; engine charges ≈ one day ≈ **$72**
(test floor: 10-day accrual ≈ $724, still violated).
**Fix:** daily accrual in the t-loop:
`short_mv = Σ|shares[t,n]|·close[t,n] for shares<0; cash[t] -= borrow_bps/1e4 * short_mv / 252`,
and drop the per-trade `qty<0` charge (or restrict to net-short increases).

## P0-6 Benchmark CAGR = mean×252 — `test_benchmark_cagr_is_geometric`
Line ~439: `bench_cagr = bmean * 252` (arithmetic), while strategy CAGR is
geometric — apples-to-oranges, flatters benchmark by ~½σ²/yr.
**Hand calc:** alternating ±10% daily, 252 returns: each pair ×0.99 ⇒
b_end/b_0 = 0.99¹²⁶ = 0.2821, true CAGR = **−71.81%**. Engine: mean=0 ⇒
reported CAGR = **0.00%** for a benchmark that lost 72%.
**Fix:** `bench_cagr = (benchmark_close[-1]/benchmark_close[0]) ** (252.0/len(brets)) - 1.0`.
⚠ The in-flight working-tree fix uses exponent `252/max(m_b-1,1)` — off by one
(gives −71.96%); denominator must be the number of RETURNS, not points−1−1.

---

Files added (no app/ code touched):
`tests/submission_audit/{conftest.py, _audit_helpers.py, test_p0_1_same_open_lookahead.py, test_p0_2_sizing_uses_cash_not_equity.py, test_p0_3_infeasible_exposure_silent.py, test_p0_4_spread_not_charged.py, test_p0_5_borrow_not_daily.py, test_p0_6_benchmark_cagr_arithmetic.py, P0_AUDIT_REPORT.md}`
