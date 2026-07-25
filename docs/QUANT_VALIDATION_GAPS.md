# Quant-Grade Statistics & Robustness Audit
**Project:** OAI_Build_Week  
**Scope:** `app/break_test/quant_validation.py`, `app/break_test/oos_validation.py`, `app/break_test/metrics.py`, `app/break_test/production_audit.py`, `app/robustness_product.py`  
**Reporter:** Automated audit  
**Date:** 2026-07-21

---

## 1. Executive Summary

Current quant validation surface is **research-grade** but **not institutional-grade**. The codebase already has a working `Sharpe/Sortino/Calmar/VaR/CVaR/alpha/beta/profit_factor` family, walk-forward validation, CPCV, embargo concepts, PSR, and deflated Sharpe. However, several components are either stubbed, inconsistent, or missing entirely. A Jane Street quant trading rejection stack would fail this system at **multiple-testing control** and **OOS inference robustness** before reaching performance metrics.

Highest-priority blockers — what gets manuscripts rejected or PMs moving to the next idea:

1. **No valid multiple-testing correction beyond BH in CPCV runs** — SPA / White RC / MCS completely absent.
2. **No bias-corrected sharpes with reported confidence bands on OOS metrics** — deflated Sharpe exists but is not coupled with bootstrap confidence intervals or bias-corrected annualization.
3. **CPCV / nested CV implementation is fragile and untested** — `combininations_attempted` is misnamed, pruning logic is heuristic, and there are zero unit tests for nested parameter stability or edge cases.
4. **State-dependent robustness is heuristics, not a validated state-conditional model** — regime-aware weighting exists but no state-conditional attribution or Hamilton-filtered state persistence.
5. **No Type I error control** — there is no probability of profitable backtest, no DSB/SDB, so we cannot bound overfit probability.

Nice-to-have (needed to close the gap with AQR-style rigor):

- Conditional performance attribution (sector/style/regime)
- Robustness-ranking family (parameter sensitivity hash tables, copula-based stability)
- Flajolet-Karlin SDB / de Prado DSB
- Purged K-fold CV as a first-class API surface

---

## 2. Current Capability Inventory

| Module | Present / Stub / Absent | Notes |
|--------|------------------------|-------|
| `metrics.py:backtest_metrics` | **Present** | Sharpe, Sortino, Calmar, VaR, CVaR, alpha, beta, profit_factor, TCA, equity curve. No bootstrap CI or bias correction. |
| `oos_validation.py:walk_forward_validation` | **Present** | Embargo, anchored/unachored, adversarial mutation, regime-aware cosine weighting, Sharpe CI, PSR, deflated Sharpe. No purged K-fold overrides for walk-forward. |
| `oos_validation.py:combinatorial_purged_cross_validation` | **Partial** | Blocked CPCV with embargo pruning, nested param selection, BH p-values, adversarial mutation. No SPA / RC / MCS. `combinations_attempted` typo bug. |
| `quant_validation.py:sensitivity_analysis` | **Present** | Grid search over parameter ranges, synthetic regime forward test, heuristic robustness score. |
| `quant_validation.py:worst_case_attribution` | **Present** | Regime worst-case sorting and turnover consistency. No state-conditional attribution. |
| `app/robustness_product.py:evaluate_sma_robustness` | **Present** | Standalone product wrapper around `_metrics`. Not integrated into CPCV/OOS. |
| `production_audit.py` | **Partial** | Threshold flags, reproducibility pack, PCA regime persistence. No CVMD-5-style multiple testing guard. |

---

## 3. Gap Analysis — Quant-Grade Standard vs Current Implementation

### 3.1 IMMEDIATE REJECT Stack

| # | Concept | Why Jane Street Rejects Immediately | Current Location / Status | Evidence |
|---|---------|--------------------------------------|---------------------------|----------|
| J1 | **SPA / White’s RC / MCS** | Without SPA, model comparison claims are unvalidated. A paper with multiple candidate strategies cannot publish a “winning” strategy without controlling the family-wise error rate. | Absent across repo | `tests/test_oos_validation.py` never imports SPA/Rc/MCS; no implementation exists. |
| J2 | **Bootstrap Confidence Intervals on Metrics** | A single Sharpe value with no CI is meaningless in production. Funds require 90% or 95% CIs on annualized Sharpe. | `_sharpe_ci` exists only in oos_validation for fold summary, but NOT for `backtest_metrics` outputs | `metrics.py:backtest_metrics` has `var_95_pct`, `cvar_95_pct`, but no metric CI. |
| J3 | **Deflated Sharpe with Bias Corrected Annualization** | `lin@lin@lin@lin@lin@lin@lin@lin@lin@lin@lin@lin@lin` Correction for backtest overfit. Existing implementation in oos_validation is mathematically inconsistent with current formula (uses a hack denominator `sr**2 - 1/(k-1)`). | `oos_validation.py:_deflated_sharpe` | Line 79: denominator is custom formula, not the original Bailey & López de Prado expression. |
| J4 | **CPCV Validation Integrity / Edge Case Control** | Parameter-selection contamination or pruning failures create phantom OOS results. Any quant review would notice missing combinations_attempted typo or unstable block fallback. | `oos_validation.py:combinatorial_purged_cross_validation` | Line 515: `summary["combinations_attempted"] = len(list(itertools.combinations(...)))`. This assumes 3-combinations of current trial blocks but uses `min(trial_blocks, 3)` silently — fallacious and confusing. No unit tests. |
| J5 | **Nested Cross Validation Presence** | Nested CV prevents data leakage from hyperparameter optimization. Current nested mode exists but has zero test coverage and simplifies inner loop to a single C-score instead of full CSE. | `oos_validation.py:_select_nested_params` | Uses `_score_candidate` which is mean/std of OOS sharpes, not full nested OOS summary. |
| J6 | **State-Dependent Robustness / Regime Labeling** | A strategy only beating synthetic random regimes is not acceptable. State-dependency must be evidenced via comparison against actual (not synthetic) regime labels and Hamilton filter. | `quant_validation.py:_quick_forward_test` uses fixed static regime dictionaries; regime-awareness in WFV is cosine-similarity state classification | No evidence of out-of-sample regime detection or state-conditional attribution. |

### 3.2 NICE-TO-HAVE Foundations

| # | Concept | Current Location / Status | Notes |
|---|---------|---------------------------|-------|
| N1 | **Flajolet-Karlin SDB** | Absent | SDB bounds the probability that a maximum observed Sharpe is spurious. |
| N2 | **de Prado DSB / Probability of Backtest Overfit** | Absent | Only `_deflated_sharpe` partial proxy exists. |
| N3 | **State-Dependent Robustness Ranking** | Partial | Robstness family only returns highest/lowest; no copula or Friedman ranking. |
| N4 | **Robustness Ranking Family** | Partial | `_stability_metrics` is 2-point spread only. No MCS-style ranking or non-parametric tests. |
| N5 | **Conditional Performance Attribution** | Absent | TCA is present but attribution by sector/regime not implemented. |
| N6 | **Purged K-Fold CV** | Partial | Purge exists within CPCV blocks; not a standalone K-fold CV. |
| N7 | **Bootstrap Confidence Intervals on Metrics** | Absent | `metrics.py` lacks bootstrap CI for Sharpe, Sortino, Calmar. |

---

## 4. Prioritized Remediation Plan (12 Hours)

### Hour 1 — Sharpe Inference Contract
**Goal:** Make OOS metrics publishable.
- [ ] `app/break_test/metrics.py`: Add `bootstrap_metric_ci(prices, positions, fn, n_bootstrap=2000, alpha=0.05)` returning bias-corrected estimate + CI. Start with Sharpe, Sortino, Calmar.
- [ ] `app/break_test/metrics.py`: Expose metric CIs through `backtest_metrics` key `sharpe_ci_95`, `sortino_ci_95`, `calmar_ci_95`.
- [ ] `tests/test_quant_data_pipeline.py` or new `tests/test_robustness_metrics.py`: basic bootstrap CI sanity tests.

### Hours 2-3 — Multiple Testing Family
**Goal:** Replace BH-only with SPA / RC / MCS.
- [ ] New file `app/break_test/multi_test.py`:
  - `white_reality_check(strategy_returns_list, benchmark_returns)`
  - `spa_test(strategy_returns_list, benchmark_returns)`
  - `mcs_selection(strategy_returns_list)` using MCS p-values
- [ ] `app/break_test/oos_validation.py:combinatorial_purged_cross_validation`: integrate MCS into fold-level combination selection.
- [ ] `app/api/app.py`: expose `/api/quant/multi-test` endpoint.

### Hours 3-4 — Deflated Sharpe Repair
**Goal:** Fix deflation math.
- [ ] `app/break_test/oos_validation.py:_deflated_sharpe`: Replace denominator with exact Bailey & López de Prado (2014) deflated Sharpe.
- [ ] Add `bias_corrected_sharpe` helper from bootstrapped annualization.
- [ ] `tests/test_oos_validation.py`: unit test for deflated Sharpe on known synthetic data.

### Hour 5 — CPCV Integrity Patch
**Goal:** Eliminate pruning and typo bugs.
- [ ] `app/break_test/oos_test.py` (new): extract CPCV block generator and combination iterator.
- [ ] Fix `combinations_attempted` typing; ensure `block_width` experimentation no longer hides failed folds.
- [ ] `app/break_test/oos_validation.py`: Add CPCV exhaustiveness flag when blocks <= 5.

### Hour 6 — Flajolet-Karlin SDB + DSB
**Goal:** Bound probability of spurious maximum Sharpe.
- [ ] New file `app/break_test/overfit_bounds.py`:
  - `flajolet_karlin_sdb(n_strategies, k_candidates, sharpe)`
  - `deprado_dsb(s past_returns, n_candidates, sharpe)`
- [ ] Expose API `api/quant/overfit-bounds` or integrate into CPCV summary.

### Hours 7-8 — State-Dependent Robustness Ranking
**Goal:** Replace heuristic robustness_score with a validated ranking.
- [ ] `app/break_test/quant_report.py` (new): `rank_strategies(results)` using Friedman test or robust ranks.
- [ ] `app/break_test/quant_validation.py`: replace `_robustness_score` with multi-criteria lexicographic ranking by regime efficacy, tail sensitivity, and turnover-normalized Sharpe.
- [ ] `app/break_test/quant_validation.py:sensitivity_analysis`: include `rank_family` and `nfailed_regimes` in output.

### Hour 9 — Conditional Performance Attribution
**Goal:** Regulatory-ready explainability.
- [ ] `app/break_test/attribution.py` (new):
  - `attribution_by_regime(prices, positions, regimes)`
  - `conditional_performance_attribution(pnl_grid, factors)` factor-neutral attribution.
- [ ] Integrate `_feature_vector` regime features into WFV summary.

### Hour 10 — Purged K-Fold CV + Nested CV Integrity
**Goal:** First-class purged K-fold API.
- [ ] `app/break_test/cross_val.py` (new):
  - `purged_k_fold_cv(prices, k=5, embargo=5, anchored=True)`
- [ ] `app/break_test/oos_validation.py:walk_forward_validation`: support fallback to K-fold when `max_folds=5`.
- [ ] Fix `_select_nested_params` to use full CV loop summary, not scaled Bechmark Sharpe.

### Hour 11 — Test Wave
**Goal:** ≥80% test coverage for quant validation paths.
- [ ] `tests/test_oos_validation.py`: expand tests for white RC, SPA, MCS, purged K-fold, nested CPCV.
- [ ] `tests/test_robustness_metrics.py`: bootstrap CI, deflated Sharpe repair, SDB/DSB.
- [ ] `tests/test_quant_post_trade_attribution.py`: conditional attribution API tests.

### Hour 12 — Documentation / Prod Audit Integration
**Goal:** Ship audit package.
- [ ] `docs/QUANT_VALIDATION_GAPS.md` — this audit.
- [ ] `app/break_test/production_audit.py`: add threshold violations for SPA p-values, bootstrap CI coverage, deflated Sharpe lower-bound.
- [ ] `README.md`: add Quant Validation / Robustness section with API examples.

---

## 5. File Target Map

| Action | File |
|--------|------|
| **Edit** | `app/break_test/metrics.py` |
| **Edit** | `app/break_test/oos_validation.py` |
| **Edit** | `app/break_test/quant_validation.py` |
| **Create** | `app/break_test/multi_test.py` |
| **Create** | `app/break_test/overfit_bounds.py` |
| **Create** | `app/break_test/quant_report.py` |
| **Create** | `app/break_test/attribution.py` |
| **Create** | `app/break_test/cross_val.py` |
| **Edit** | `app/api/app.py` |
| **Edit** | `tests/test_oos_validation.py` |
| **Edit** | `tests/test_quant_oos.py` |
| **Create** | `tests/test_robustness_metrics.py` |
| **Create** | `tests/test_quant_post_trade_attribution.py` |
| **Edit** | `app/break_test/production_audit.py` |
| **Edit** | `README.md` |
| **Create** | `docs/QUANT_VALIDATION_GAPS.md` |

---

## 6. Risk-Adjusted Conclusion

**Do not pass `app/break_test/quant_validation.py` to production PMs without Hours 1-4 completed.** Those hours eliminate the highest-probability rejection: *“You have no multiple-testing correction and no confidence bands on metrics.”* The remaining nice-to-have items (N1-N7) create a defensible institutional-grade validation pipeline but do not individually block advancement.
