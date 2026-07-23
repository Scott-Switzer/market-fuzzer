# MVP Quant Metrics & Statistical Safeguards: Exact Definitions, Edge-Case Behavior, and Repo Mapping

Precise definitions below map each requested metric to the current code path in `OAI_Build_Week`. Where the repo already implements the formula, the exact module/function is cited. Where it is missing or ambiguous, **GAP** is flagged with suggested formula repairs for the MVP.

---

## 1. Return Metrics

### 1.1 Total Return
- **Formula**: `total_return_pct = (E_t / E_0 - 1) * 100`
- **Repo definition**: `app/break_test/metrics.py::backtest_metrics` line 580: `round((float(equity[-1]) - 1) * 100, 2)`
- **Edge cases**:
  - If `len(prices) < 2` → `equity = [1.0]`; returns `0.0`.
  - If `equity[-1] <= 0` → returns large negative pct; should clamp to `-100.0` or return `np.nan`.
  - **GAP**: No explicit defensive clamp for obsurd out-of-bounds values if costs exceed equity.

### 1.2 CAGR (Compound Annualized Growth Rate)
- **Formula**: `CAGR = (E_t / E_0) ** (252 / T) - 1` for daily bars, where `T = n - 1` returns or bars of equity.
- **Repo mapping**: Partial. `arena.py::_annualized_return` lines 491-495 uses `growth ** (252 / len(values)) - 1`, but `backtest_metrics` returns `total_return_pct` only.
- **Edge cases**:
  - `E_0 <= 0`: return `-1.0` or `np.nan`. Current code assumes positive `E_0` via `px[:-1]`.
  - `T < 1`: return `0.0` and emit diagnostic.
  - Negative equity path: CAGR is only defined for positive terminal equity.
- **GAP**: No `cagr_*` key in `backtest_metrics` output dict. Add:
  ```python
  if equity.size < 2 or float(equity[0]) <= 0:
      cagr = 0.0
  else:
      cagr = float(equity[-1] / equity[0]) ** (252.0 / max(int(equity.size - 1), 1)) - 1.0
  ```

### 1.3 Annualized Mean Return (simple proxy)
- **Formula**: `mean_r = np.mean(strategy_returns); annualized = mean_r * 252`
- **Repo**: implicitly computed inside Sharpe/Sortino numerators.
- **Edge case**: If `strategy_returns` is empty, returns `0.0`.

---

## 2. Risk / Reward Risk-Adjusted Metrics

### 2.1 Volatility (Annualized)
- **Formula**: `sigma = std(strategy_returns, ddof=1) * sqrt(252)`
- **Repo**: computed inline in `backtest_metrics` lines 522-524, but **not returned**.
- **Edge cases**:
  - `len(strategy_returns) <= 1` → sigma = `0.0`.
  - If Bollinger-style zero std (flat returns), returns `0.0`.
- **GAP**: Add `volatility_pct` to output dict:
  ```python
  volatility_pct = std * 100.0 * math.sqrt(252)
  ```

### 2.2 Sharpe Ratio
- **Formula**: `sharpe = mean(r) / std(r, ddof=1) * sqrt(252)` if `std > 0` else `0.0`.
- **Repo**: `backtest_metrics` lines 522-524. `bootstrap_metric_ci` lines 58-73 implement the same.
- **Edge cases**:
  - Zero std → `0.0`; codebase already guards with `if std > 0`.
  - Negative mean with positive std → negative Sharpe preserved.
  - `n < 3` in bootstrap → `0.0` (lines 59-60).

### 2.3 Sortino Ratio
- **Formula**: `mean(r) / downside_std * sqrt(252)` where `downside_std = std(r[r < 0], ddof=1)`.
- **Repo**: `backtest_metrics` lines 525-527; special case: `-1.0` when `mean <= 0` and `downside_std == 0`? Current code:
  ```python
  sortino = mean / downside_std * math.sqrt(252) if downside_std > 0 else (-1.0 if mean <= 0 else 0.0)
  ```
- This is a **nonstandard sentinel**: hit if no downside observations exist. Documented behavior should be: return `0.0` when there are no losses (perfect), and `-1.0` only when losses exist but have zero variance (degenerate path).

### 2.4 Calmar Ratio
- **Formula**: `calmar = (E_t / E_0 - 1) / max(-1, max_drawdown)`
  - Current code uses equity normalized to start at 1: `(float(equity[-1]) - 1) / (-max_dd)`.
- **Repo**: `backtest_metrics` lines 528-529.
- **Edge cases**:
  - `max_dd >= 0` (no drawdown) → `0.0`.
  - Returning `0.0` when strategy is profitable with zero drawdown is misleading; bonus points for returning `np.inf` with a `is_infinite` flag.
  - **GAP**: No boolean flag for infinity/invalid cases. Document.

### 2.5 Downside Deviation
- **Formula**: `downside_std = std(r[r < 0], ddof=1)` if `len(downside) > 1` else `0.0`.
- **Repo**: computed inside `backtest_metrics` lines 525-526; **not returned** as standalone metric.
- **GAP**: Add `downside_deviation_pct`:
  ```python
  downside_deviation_pct = float(downside_std * 100.0 * math.sqrt(252))
  ```

### 2.6 Win / Loss Counts
- **Win**: trade return > 0; **Loss**: trade return < 0; **Breakeven**: = 0 excluded.
- **Repo**: `backtest_metrics` lines 545-548 derives counts implicitly via list comprehensions, but returns only `win_rate_pct` and `profit_factor`. **Win count** and **loss count** are not returned.
- **GAP**: Add `win_count`, `loss_count`, `breakeven_count`.

### 2.7 Win Rate (Hit Rate)
- **Formula**: `win_rate_pct = wins / total_trades * 100`.
- **Repo**: `backtest_metrics` line 545-549. `overfit_bounds.py` line 185 also computes it.
- **Edge cases**:
  - `trade_returns == []` → `0.0`.
  - **GAP**: Missing win rate in basis points or decimal; current output is pct only.

### 2.8 Profit Factor
- **Formula**: `PF = sum(wins) / |sum(losses)|` if both non-empty, else `0.0`.
- **Repo**: `backtest_metrics` line 548.
- **Edge cases**:
  - Wins but no losses → returns `0.0`. Should return `np.inf` or `999.99`.
  - Losses but no wins → returns `0.0` (correct, but document as “untradeable”).
- **GAP**: No `pf_flag - infinity` guard.

### 2.9 Expectancy (Average Trade Return)
- **Formula**: `E[R] = mean(trade_returns)`.
- **Repo**: `backtest_metrics` line 595 returns `expectancy`.
- **Edge cases**:
  - `trade_returns == []` → `0.0`.
- Also see `overfit_bounds.py::_robustness_score`, which uses trade-return stats for scoring.

### 2.10 Avg Trade Return Pct
- **Formula**: `avg_trade_return_pct = mean(trade_returns) * 100`.
- **Repo**: `backtest_metrics` line 594.
- **Edge cases**:
  - Same as expectancy.

---

## 3. Drawdown

### 3.1 Maximum Drawdown (MDD)
- **Formula**: `max_dd = min(equity / peak - 1)` over time `t`.
- **Repo**: `backtest_metrics` lines 519-528.
- **Edge cases**:
  - Equity never falls below peak → `max_dd = 0.0`; Calmar → `0.0`.
  - Equity hits zero → `max_dd = -100.0` (i.e., `min(-1, ...)`). Document protocol.

### 3.2 MDD Duration
- **Formula**: Maximum number of consecutive bars where `equity / peak - 1 < 0`.
- **Repo**: `backtest_metrics` lines 530-534:
  ```python
  under = np.where(drawdown < 0)[0]
  lengths = np.diff(np.where(np.concatenate(([under[0]], np.diff(under) > 1, [True])))[0])
  ```
- **Edge cases**:
  - Empty DD array → `0.0`.
  - Single underwater bar → duration = `1`.

### 3.3 Underwater Curve (optional)
- **Repo**: not exposed. `calibration/synthetic_market.py` likely owns synthetic regime scenarios, but no underwater curve.

---

## 4. Risk: VaR / CVaR

### 4.1 Historical 95% VaR
- **Formula**: `VaR_95 = percentile(strategy_returns, 5)` (5th percentile).
- **Repo**: `backtest_metrics` line 550:
  ```python
  var_95 = float(np.percentile(strategy_returns, 5))
  ```
- **Edge cases**:
  - Empty series → `0.0`.
  - All zeros → `0.0`.
  - **GAP**: No support for 99% VaR / 1-day vs 10-day; no scaling or normalizing.
- Also present in `gov` slash `analytics` but not the same structure.

### 4.2 Historical CVaR (Expected Shortfall) 95%
- **Formula**: `CVaR_95 = mean(strategy_returns[strategy_returns <= VaR_95])`.
- **Repo**: `backtest_metrics` line 551.
- **Edge cases**:
  - No returns below VaR → CVaR equals VaR.
  - Empty series → `0.0`.

### 4.3 Parametric VaR (missing)
- **GAP**: No Gaussian or Cornish-Fisher parametric VaR. Add Cornish-Fisher expansion using skew/kurtosis if available from PSR helper.

### 4.4 Tail Ratio
- **Formula**: `tail_ratio = abs(mean_positive_return / mean_negative_return)` or `95th percentile / 5th percentile`.
- **Repo**: not present.
- **GAP**: No `tail_ratio` implementation. Define and add. Edge: if no negative returns fall back to `np.inf`.

---

## 5. Turnover & Costs

### 5.1 Turnover
- **Formula**: `turnover = sum(|Δposition_t|)` over all bars.
- **Repo**: `backtest_metrics` line 518: `float(np.sum(np.abs(np.diff(pos, prepend=0.0))[:-1]))`.
- **Edge cases**:
  - `len(pos) <= 1` → `0.0`.
  - Zero trading → `0.0`.

### 5.2 Transaction Cost Model (TCA)
- **Granular cost components**: spread, temporary/permanent impact, toxicity/Bid-Ask bounce, borrow fee, exchange fee, completion-rate penalty.
- **Repo**:
  - `app/break_test/costs.py` defines `TransactionCostModel`, `almgren_chriss_impact_bps`, `toxicity_bps`, `borrow_fee_bps_for_short`.
  - `app/break_test/metrics.py::compute_turnover_cost` returns per-bar cost fraction.
  - `app/break_test/metrics.py::tca_by_bucket` buckets by participation rate.
- **Edge cases**:
  - `exchange_spec is None` → flat `2 bps` returned (line 192).
  - `vol is None` → falls back to `_estimate_daily_vol` from prices.
  - Zero trade size → zero cost (guard at line 239).
  - Short inventory detection: `np.maximum(-positions_trim, 0.0)` lines 263-276.
- **GAP**: No consolidated `total_turnover_cost_pct` or `total_cost_bps` at the backtest summary level.

### 5.3 Implementation Shortfall (IS)
- **Formula**: `IS_bps = direction * (avg_execution_price - arrival_price) / arrival_price * 10_000` plus opportunity cost.
- **Repo**: `app/governance.py` names it at the enterprise layer. `app/break_test/metrics.py::compute_tca_metrics` returns `slippage_vs_arrival`, `opportunity_cost`, `completion_rate_penalty_bps`.
- **GAP**: `backtest_metrics` output does not expose a single `implementation_shortfall_bps` key; it returns components. Add a wrapped summary:
  ```python
  implementation_shortfall_bps = (
      tca["slippage_vs_arrival"]
      + tca["opportunity_cost"]
      + tca["completion_rate_penalty_bps"]
  )
  ```

---

## 6. Exposure & Concentration

### 6.1 Gross Exposure
- **Formula**: `gross_exposure = Σ_j |w_j|`, where `w_j` are asset weights.
- **Repo**: `arena.py` lines 572-573:
  ```python
  gross_exposure = sum(abs(current_positions[asset]) for asset in challenge.assets)
  ```
- **Edge cases**:
  - Sum of positions may be zero if flat long/short net, but gross > 0.
  - **GAP**: Not computed in `backtest_metrics`. Single-asset backtests assume `position` in `[-1, 1]`, so gross equals `|position|`; add flag.

### 6.2 Net Exposure
- **Formula**: `net_exposure = Σ_j w_j`.
- **Repo**: `arena.py` lines 573:
  ```python
  net_exposure = sum(current_positions.values())
  ```
- **Edge cases**:
  - Long bias => positive; short bias => negative.
  - **GAP**: Not present in `backtest_metrics`.

### 6.3 Concentration (Largest Asset Exposure / Largest Asset Concentration)
- **Formula**: `concentration = max_j |w_j| / max(Σ_j |w_j|, 1e-9)`.
- **Repo**: `arena.py` lines 608-616.
- **Edge cases**:
  - Division by zero guarded by `1e-9`.
  - **GAP**: `backtest_metrics` has no asset-level dimension, so this metric is only relevant for multi-asset futures. Add to multi-asset forward module if needed.

### 6.4 Top-N Concentration / HHI (missing)
- **GAP**: No HHI or top-k concentration metric. Add `concentration_hhi = Σ_j w_j²` (Herfindahl).

---

## 7. Beta & Factor Exposure

### 7.1 Beta
- **Formula**: `cov(R_strat, R_bench) / var(R_bench)`.
- **Repo**: `backtest_metrics` lines 539-542. Uses `np.cov` with `ddof=1` and `np.var` with `ddof=1`. Benchmark default is asset return series itself.
- **Edge cases**:
  - Zero bench variance → `0.0`.
  - **GAP**: Single-asset benchmark is fine, but no multi-factor exposure.

### 7.2 Alpha
- **Formula**: `alpha = mean(R_strat - R_bench)`.
- **Repo**: `backtest_metrics` line 543: `float(np.mean(strategy_returns - bench_returns))`.
- **Edge cases**:
  - Negative alpha is valid but should be framed as “tracking error drag.”
  - Alpha is a simple mean difference, not risk-adjusted. Consider reporting as `alpha * 252` to match `backtest_metrics` output.

### 7.3 Factor Exposures (multi-factor)
- **Repo**: not implemented. There is no regression on Fama-French or PCA factors.
- **GAP**: Add optional `factor_loadings` array for external factor series. For MVP, fallback to single-benchmark beta is acceptable if documented.

---

## 8. Distributional Stats

### 8.1 Skewness
- Formula: `m3 / std³` where `m3 = mean((r - mean(r))³)`.
- **Repo**: `app/break_test/overfit_bounds.py::deprado_dsb` lines 88-94 compute skew for DSB; `app/break_test/oos_validation.py::_deflated_sharpe` lines 90-94 also compute skew. Neither exports it to `backtest_metrics`.
- **GAP**: Add `skewness` to `backtest_metrics` output.

### 8.2 Kurtosis
- **Formula**: `m4 / std⁴` where `m4 = mean((r - mean(r))⁴)`. **Excess kurtosis** = kurtosis - 3.
- **Repo**: same paths as skew.
- **GAP**: Add `kurtosis` and `excess_kurtosis` to `backtest_metrics`.

### 8.3 Tail Ratio
- **Formula**: `tail_ratio = p95(|r|) / p05(|r|)` or `mean_positive_return / |mean_negative_return|`.
- **Repo**: not present.
- **GAP**: Implement and expose as `tail_ratio`.

---

## 9. Statistical Safeguards

### 9.1 Bootstrap Confidence Intervals (Block / Standard)
- **Formula**: For each bootstrap sample, recompute metric such as annualized Sharpe. CI = percentiles of bootstrap distribution.
- **Repo**:
  - `app/break_test/metrics.py::bootstrap_metric_ci` lines 31-92: moving-block bootstrap for Sharpe/Sortino/Calmar. Hard cap `n_bootstrap` at 2000.
  - `app/break_test/oos_validation.py::bias_corrected_sharpe` lines 140-172: simple bootstrap Sharpe with bias correction.
- **Edge cases**:
  - `px.size < 5` (block bootstrap) → returns `{0.0, 0.0, 0.0, 0.0}`.
  - `n_bootstrap > 1000` → warning + hard cap to 2000.
  - **GAP**: No automatic coupling of `bootstrap_metric_ci` into `backtest_metrics` dict. Add `sharpe_ci_low/high`, `sortino_ci_low/high`, `calmar_ci_low/high`.

### 9.2 Probabilistic Sharpe Ratio (PSR)
- **Formula (vendor-neutral)**: `PSR = Φ((SR̂ − SR*) / σ̂_SR)` where Φ is the standard normal CDF, `SR̂` is observed annualized Sharpe, `SR*` is target Sharpe, and `σ̂_SR` uses LdP2014 ANOVA-style variance with skew/kurtosis corrections.
- **Repo**:
  - `app/break_test/oos_validation.py::_psr_vs_zero` lines 53-58: PSR vs zero target.
  - `app/break_test/oos_validation.py::_psr_vs_benchmark` lines 62-68: PSR vs benchmark Sharpe with optional covariance.
- **Edge cases**:
  - `n <= 1` → `0.0`.
  - `math.isnan(sharpe)` → `0.0`.
- **GAP**: Not exposed in `backtest_metrics`. Add `psr_vs_zero` and `psr_vs_benchmark` if benchmark supplied.

### 9.3 Deflated Sharpe Ratio (DSR)
- **Formula**: `DSR = Φ((SR̂ − E[max SR_k|H0]) / σ̂_SR)` where `E[max]` is expected max under global null, computed via `(1 - γ) Φ^{-1}(1-1/k) + γ Φ^{-1}(1-1/(k e))` with Euler’s constant γ.
- **Repo**: `app/break_test/oos_validation.py::_deflated_sharpe` lines 71-110.
- **Edge cases**:
  - Empty array → `0.0`; single element → `0.0`.
  - Trials count wrong → `trials = max(k, 1)`.
- **Wrapper**: `app/break_test/metrics.py::deflated_sharpe_ratio` lines 652-668 calls `_deflated_sharpe` and adds `passes_hardening`.
- **GAP**: No `deflated_sharpe` in `backtest_metrics` dict for a single backtest path.

### 9.4 Flajolet–Karlin Sharpe Deflation Bound (SDB)
- **Formula**: `threshold = sqrt(2 log(n * k)) / sqrt(n_obs) * sqrt(252)`. `p_spurious = 1 - Φ((SR̂ - threshold) / σ)`.
- **Repo**: `app/break_test/overfit_bounds.py::flajolet_karlin_sdb`.
- **Edge cases**:
  - Multiplicative blow-up if `n_obs < 2`: `se_ann = se * sqrt(252)` (lines 63-64).
- **GAP**: Not called from `backtest_metrics`.

### 9.5 PBO (Probability of Backtest Overfitting)
- **Formula**: Block-bootstrap resample of Sharpe series; count proportion where optimal strategy in bootstrap != optimal in original. `PBO = (overfit+1)/(n_bootstrap+1)`.
- **Repo**: `app/break_test/metrics.py::estimate_pbo` lines 680-709.
- **Edge cases**:
  - Empty sharpe series → default `PBO=1.0`, `rank_percentile=0.0`.
  - **GAP**: No CI around PBO; no lower/upper bound fields. Document as point estimate.

---

## 10. Sharpe-Adjusted Support Metrics

### 10.1 Turnover-Adjusted Sharpe
- **Formula**: `TAS = Sharpe / sqrt(max(turnover, ε))`.
- **Repo**: `app/break_test/metrics.py::turnover_adjusted_sharpe` lines 712-738.
- **Edge cases**:
  - Zero turnover → adjusts by `sqrt(1e-9)`.
  - **GAP**: Duplicate logic exists in `overfit_bounds.py::_robustness_score`. Unify by importing `turnover_adjusted_sharpe`.

### 10.2 Kelly-Optimal Fraction & Adjusted Return
- **Formula**: `f* = mean(r) / var(r)`, clamped to `[-1, 1]`.
- **Repo**: `app/break_test/metrics.py::kelly_adjusted_returns` lines 741-769.
- **Edge cases**:
  - Zero variance → Kelly fraction clamped via `max(var, 1e-16)`.
  - Negative Kelly with positive mean is possible for negative-skew strategies; clamp is acceptable for MVP.

---

## 11. Audit / Production Safeguards

### 11.1 Rule Flags & Threshold Violations
- **Repo**: `app/break_test/production_audit.py::rule_flag` and `threshold_violations`.
- **Built-in thresholds**:
  - `total_return_pct > 0`
  - `sharpe > 0`
  - `win_rate_pct > 35`
- **Edge cases**:
  - Strategy throws → captured as `{"error": str(exc)}`.
- **GAP**: Need explicit thresholds for new metrics (CAGR > 0, Sortino > 0, VaR bound, concentration < 75%, turnover < X).

### 11.2 Reproducibility Pack
- **Repo**: `app/break_test/production_audit.py::-reproducibility_metadata` and `build_repro_pack`.
- **Edge cases**:
  - Mutable mutable exchange fields captured; missing exchange fields return empty dict.

---

## 12. Summary Table: Existing vs Missing per Module

| Metric | Existing Formulas | Returns | Edge Cases Guarded | Params / Flags | Module(s) |
|---|---|---|---|---|---|
| Total return | ✔ | ✔ | partial | none | `metrics.py` |
| CAGR | partial | ❌ | ❌ | none | `arena.py` |
| Volatility | inline compute | ❌ | partial | none | `metrics.py` |
| Sharpe | ✔ | ✔ | ✔ | `n_bootstrap`, `seed` | `metrics.py`, `oos_validation.py` |
| Sortino | ✔ | ✔ | penalty sentinel | — | `metrics.py` |
| Calmar | ✔ | ✔ | ✓ | — | `metrics.py` |
| Max drawdown | ✔ | ✔ | ✔ | — | `metrics.py` |
| Drawdown duration | ✔ | ✔ | partial | — | `metrics.py` |
| Downside deviation | inline | ❌ | partial | — | `metrics.py` |
| VaR(95) | ✔ | ✔ | ✔ | — | `metrics.py` |
| CVaR(95) | ✔ | ✔ | ✔ | — | `metrics.py` |
| Tail ratio | ❌ | ❌ | ❌ | — | — |
| Turnover | ✔ | ✔ | ✔ | — | `metrics.py` |
| TCA / IS | ✔ components | partial | ✔ | `exchange_spec` | `metrics.py`, `costs.py`, `governance.py` |
| Gross / Net exposure | ✔ | arena-only | ✔ | — | `arena.py` |
| Concentration | ✔ | arena-only | ✔ | — | `arena.py` |
| Beta | ✔ | ✔ | ✔ | — | `metrics.py` |
| Factor exposure | ❌ | ❌ | ❌ | — | — |
| Win / Loss counts | inline | ❌ | ✔ | — | `metrics.py` |
| Hit rate | ✔ | ✔ | ✔ | — | `metrics.py` |
| Profit factor | ✔ | ✔ | exceptions | — | `metrics.py` |
| Expectancy | ✔ | ✔ | ✔ | — | `metrics.py` |
| Transaction cost | ✔ components | partial | ✔ | exchange spec | `metrics.py`, `costs.py` |
| Impl. shortfall | components | partial | ✔ | side, arrival | `metrics.py`, `governance.py` |
| Skew / Kurtosis | inline for DSR | ❌ | partial | — | `overfit_bounds.py`, `oos_validation.py` |
| Bootstrap CIs | ✔ | ❌ | ✔ | `n_bootstrap`, `seed`, `alpha` | `metrics.py`, `oos_validation.py` |
| PSR | ✔ | ❌ | ✔ | `benchmark_sharpe`, `n` | `oos_validation.py` |
| Deflated Sharpe | ✔ | partial | ✔ | `n_trials`, `seed` | `metrics.py`, `oos_validation.py` |
| PBO | ✔ | partial | ✔ | `n_bootstrap`, `seed` | `metrics.py` |

---

## 13. Recommended MVP Deliverable

Add a thin wrapper `backtest_metrics_v2` (or extend the existing dict return) that:

1. Computes and returns:
   - `cagr_pct`
   - `volatility_pct`
   - `downside_deviation_pct`
   - `skewness`, `excess_kurtosis`
   - `tail_ratio`
   - `gross_exposure`, `net_exposure`, `concentration` (when multi-asset passable)
   - `sharpe_ci_low/high`, `sortino_ci_low/high`, `calmar_ci_low/high`
   - `psr_vs_zero`, `psr_vs_benchmark`
   - `deflated_sharpe`
   - `implementation_shortfall_bps`
   - `win_count`, `loss_count`
   - `bootstrap_ci` dict

2. Refactors duplicated Sharpe volatility logic:
   - Share a single `_ann_sharpe(returns)` helper used by `backtest_metrics`, `_quick_forward_test`, and `_backtest_metrics` in `overfit_bounds.py`.

3. Adds explicit safeguards:
   - Clamp `total_return_pct` to `[-100, 10_000_000]` after equity crunch.
   - Return `np.nan` or document sentinels when division-by-zero is unavoidable.
   - Add `flags: dict[str, str]` for `calmar_infinite`, `pf_infinite`, `sortino_perfect`.

4. Adds MVP thresholds to `production_audit.py:
   - `cagr_pct > 0`
   - `sortino > 0`
   - `max_drawdown_pct > -50`
   - `tail_ratio > 1.0`
   - `concentration < 0.75` (if multi-asset)
   - `psr_vs_zero > 0.5`
   - `deflated_sharpe > 0.05`

---

## 14. Exact Formula Catalog (canonical for MVP)

```
r_t = p_{t} / p_{t-1} - 1
R_t = pos_{t-1} * r_t - cost_t
E_t = E_{t-1} * (1 + R_t)

CAGR = (E_T / E_0) ^ (252 / T) - 1

sigma = std(R, ddof=1) * sqrt(252)
Sharpe = mean(R) / sigma * sqrt(252)   if sigma>0 else 0

downside = R[R < 0]
downside_sigma = std(downside, ddof=1) * sqrt(252)
Sortino = mean(R) / downside_sigma * sqrt(252)   special sentinel when no losses

peak_t = max_{s<=t} E_s
DD_t = E_t / peak_t - 1
MaxDD = min(DD_t)

Calmar = (E_T / E_0 - 1) / max(-1, MaxDD)

VaR_q = percentile(R, q)
CVaR_q = mean(R[R <= VaR_q])

Turnover = sum(|pos_t - pos_{t-1}|)

Alpha = mean(R_strat - R_bench)
Beta  = cov(R_strat, R_bench) / var(R_bench)

Hit rate = wins / total_trades
Profit factor = sum(wins) / |sum(losses)|
Expectancy = mean(trade return)

Implementation shortfall = slippage_vs_arrival + opportunity_cost + completion_penalty

Skew = m3 / sigma^3,  m3 = mean((r - mean(r))^3)
Excess kurt = m4 / sigma^4 - 3

TailRatio = p95(r) / |p5(r)|

Block bootstrap CI:
  B samples of moving block length b=ceil(T/20)
  For each sample: metric(sample)
  CI = percentile(metrics, [alpha/2, 1-alpha/2])

PSR vs target SR*:
  z = (SR_hat - SR*) / sqrt((1 - skew*SR + (kurt-1)/4 * SR^2) / (T-1))
  PSR = Φ(z)

Deflated Sharpe:
  SR0 = (1-γ) Φ^{-1}(1-1/k) + γ Φ^{-1}(1-1/(k e))
  DSR = Φ( (SR_hat - SR0) / sqrt(corrected_var) )

PBO:
  Draw b blocks, resample strategy Sharpe series.
  PBO = (# draws where argmax differs + 1) / (B + 1)
```

---

## 15. Edge-Case Behavior Contract

| Scenario | Behavior |
|---|---|
| `prices` empty or length 1 | Return zeros for all metrics. No exception. |
| All-negative returns, zero downside variance | Sortino → `-1.0` sentinel (current); document as “perfect theoretical risk-free loss stream”. |
| `equity[0] <= 0` | CAGR → `0.0`; total_return → clamped negative limit. |
| Zero trades executed | Sharpe/Sortino based on bar returns instead of trade returns; WinRate = `0.0`; PF = `0.0`. |
| `std == 0` for bar returns | Sharpe → `0.0`; not `np.inf` because the ratio is undefined. |
| `max_drawdown == 0` with positive return | Calmar → `0.0` (as implemented); document as special case for tooling. |
| NaN prices in array | Coerce with `np.asarray(..., dtype=float)`; `np.diff` yields NaN downstream → metrics propagate NaN. **GAP**: Add `nan_policy='omit'` guards. |
| `exchange_spec` missing | Default 2 bps flat cost. |
| Short inventory with no borrow schedule | Legacy flat `borrow_fee_bps` path with added spread contribution. |

---

## 16. Files Creating or Modifying

None were modified during this audit. Output is a specification document ready for hand-off to the build sprint. Recommended next step: implement `v2` wrapper in `app/break_test/metrics.py` and extend `production_audit.py` rules.
