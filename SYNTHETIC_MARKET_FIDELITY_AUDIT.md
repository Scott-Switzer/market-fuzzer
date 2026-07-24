# Synthetic Market Generation Fidelity Audit & Fix Plan

## Executive Summary

**Audited file:** `app/break_test/synthetic_market.py`  
**Volume helpers:** `app/exchange/volume_profile.py`  
**Who uses it:** `app/break_test/regimes.py`, `demo.py`, `scripts/benchmark_baseline.py`

This audit maps every deviation from institutional-quality synthetic market generation to the exact
line/target in the current codebase, ranks them by downstream impact on break-test conclusions,
and provides a time-boxed 12-hour implementation plan plus a 24-hour stretch goal.

---

## 1. Registry of Fidelity Gaps

### PRICE GENERATION

#### GAP-P1 `generate_path` ignores regime inputs entirely
- **Lines:** 239-251 in `synthetic_market.py`
- **Current behavior:** `generate_path(regime_key, seed, ...)` accepts a regime key, but the body
  draws `rng.normal(0.0, 0.015, length - 1)` and computes `prices_arr[0] * exp(cumsum(returns))`.
  The `regime_key` is only mirrored into the returned dict.
- **Institutional expectation:** Daily return distribution must be regime-conditional
  (drift, variance, tail shape, jump intensity). Ignoring regime yields identical price paths
  regardless of "calibration", which invalidates regime-based robustness tests.
- **Impact:** P0 — Every break-test world is the same data-generating process regardless of regime.

#### GAP-P2 No stochastic volatility / GARCH simulation
- **Lines:** 138-168 `_fit_garch`, 239-251 `generate_path`, 253-318 `generate_correlated_gbm_paths`
- **Current behavior:** GARCH is fit only to compute `unconditional_vol` and `standardized_residuals`.
  Neither helper simulates from the fitted GARCH variance process. Volatility is flat within a regime.
- **Institutional expectation:** Path simulation should iterate
  `sigma_t^2 = omega + alpha * eps_{t-1}^2 + beta * sigma_{t-1}^2`, feeding stochastic
  volatility into returns. This produces volatility clustering, mean reversion in vol, and
  richer drawdown distributions.
- **Impact:** P0 — Missing volatility clustering invalidates risk-estimation break-tests
  and renders tail-risk comparisons meaningless.

#### GAP-P3 Normal innovations / non-normal residuals are not used in path generation
- **Lines:** 160-168 `_compute_standardized_residuals`, 241 `rng.normal`, 293 `rng.standard_normal`
- **Current behavior:** Residuals are computed but never bootstrapped into `generate_path` or
  `generate_correlated_gbm_paths`. Both use Gaussian draws unconditionally.
- **Institutional expectation:** Use fitted standardized residuals with optional AR(1) smoothing
  (`draw_standardized_residuals` exists at line 366-376, but is unused by path generators).
  If empirical residuals are insufficient, mix in Student-t or double-exponential (Laplace)
  innovations with estimated tail parameters.
- **Impact:** P0 — Normal innovations suppress tail events, deception loss, and vol-of-vol
  spikes; strategies are not challenged enough.

#### GAP-P4 No regime-consistent variance sequencing
- **Lines:** 180-185 `_build_regimes`
- **Current behavior:** Four `RegimeSpec` objects have `vol_annual`, but `generate_path` never
  references them. `generate_correlated_gbm_paths` pulls only `mu_annual` and a constant daily
  covariance matrix.
- **Institutional expectation:** Regime persistence (duration) and Markov transition matrix should
  control variance over the horizon. A volatility regime should persist for multiple days/weeks,
  not revert after one step.
- **Impact:** P1 — Regime labels become cosmetic labels rather than calibrated DGP states;

#### GAP-P5 Missing jump / Merton-style discontinuity dynamics in generation
- **Lines:** 24-36 `RegimeSpec` defines `lambda_jump`, `mu_jump`, `sigma_jump`
- **Current behavior:** Jump parameters exist on spec objects but are never sampled or applied in
  either path generator.
- **Institutional expectation:** Poisson jump arrivals with jump-size mixture should be injected
  into return paths with probabilities calibrated by regime (`lambda_jump` per day).
- **Impact:** P1 — Losses in stress regimes are artifactually smooth; tail-risk metrics are biased.

#### GAP-P6 `generate_path` returns length `length` but loops `steps=max(10, int(length))`
- **Lines:** 239, 286
- **Current behavior:** `generate_path` uses `length` directly; `generate_correlated_gbm_paths`
  computes `steps = max(10, int(length))` but exception path on line 292 still uses steps.
- **Institutional expectation:** Consistent horizon semantics; no hidden truncation when `length < 10`.
- **Impact:** P2 — Mild, but makes back-test sample sizes non-comparable across call sites.

---

### VOLATILITY GENERATION

#### GAP-V1 Volatility persistence and mean-reversion speed are fixed to ad-hoc constants
- **Lines:** 138-158 `_fit_garch`
- **Current behavior:** Alpha, beta initialized to 0.06/0.92 and clamped min-max; optimization
  uses trivially small step sizes with no convergence diagnostics.
- **Institutional expectation:** Proper MLE or QMLE calibration with log-likelihood
  tracking, persistence bounds, and persistence-conditional on regime (higher persistence
  in high-vol regimes).
- **Impact:** P1 — Fitted volatility parameters are unreliable, undermining any vol-parameter
  sensitivity studies.

#### GAP-V2 No leverage / feedback effects
- **Lines:** 241, 293, 304
- **Current behavior:** Returns do not feedback into next-period variance beyond ad-hoc GARCH
  because the simulator doesn't use it.
- **Institutional expectation:** Risk-return feedbacks where past negative returns elevate
  next-period variance (EGARCH or GJR leverage term).
- **Impact:** P2 — Improves realism but is secondary to missing stochastic volatility entirely.

---

### VOLUME GENERATION

#### GAP-VOL1 No dynamic volume simulation in `synthetic_market.py`
- **Lines:** 118-124 re-export; 47-53 `AssetFactorConfig.liquidity_profile`
- **Current behavior:** `liquidity_profile` exists only as a forwarder to `displayed_depth_autor`
  in `volume_profile.py`. No time-series volume is generated alongside prices.
- **Institutional expectation:** Volume should be endogenous: clustered via Hawkes/ACD,
  scaled by absolute or signed returns, regime-conditioned, and autocorrelated.
- **Impact:** P0 — Break tests that assume cost/slippage functions of volume are using
  unrealistic constant/flat inputs; execution-quality conclusions are unreliable.

#### GAP-VOL2 Intraday profile helpers are deterministic and scalar
- **Lines:** 9-34 in `volume_profile.py`
- **Current behavior:** Only two deterministic deterministic profiles; no intraday
  seasonality parameterization or random variation.
- **Institutional expectation:** Parameters (U-shape amplitude, lunch-trough depth) should
  be regime-dependent and stochastically perturbed.
- **Impact:** P2 — Lower priority but blocks tight VWAP/cost modeling.

#### GAP-VOL3 Missing LOB depth, queue position dynamics, VWAP profiles
- **Lines:** 37-51 `displayed_depth_autor`
- **Current behavior:** Returns a single integer depth scaled by liquidity profile and volume weight.
  No order book depth time series, no queue position, no bid/ask asymmetry.
- **Institutional expectation:** LOB depth should co-evolve with price moves; depth drops when
  volatility spikes. Queue-position dynamics increase execution time uncertainty.
- **Impact:** P1 — Empirical execution models lack the microstructure needed for realistic
  cost estimates in break-testing.

---

### CORRELATION GENERATION

#### GAP-C1 Factor loadings matrix is mostly unimplemented / hardcoded to market beta only
- **Lines:** 337-340 `_build_asset_factor_covariance`
- **Current behavior:** Regression matrix is built from only `global_equity_market` exposure;
  all other factor loadings are set to 0 despite `FACTOR_LOADINGS`, `FACTOR_NAMES`,
  `FACTOR_ANNUAL_VOL`, `FACTOR_CORRELATIONS` being fully defined at lines 62-116.
- **Institutional expectation:** Use per-ticker `FACTOR_LOADINGS` for all 5 factors
  so cross-sectional correlation structure is diversified and realistic (size, value, momentum,
  rates exposures change correlations).
- **Impact:** P0 — Correlation matrix collapses to 1-factor market; multi-asset portfolio
  break-tests understate diversification benefit and overstate tail correlation.

#### GAP-C2 `AssetFactorConfig.price_cache_factor_loading` is never consumed
- **Lines:** 52 `price_cache_factor_loading: float | None = None`
- **Current behavior:** The field exists but `_build_asset_factor_covariance` never reads the
  `AssetFactorConfig` objects; it hardcodes loadings and macros.
- **Institutional expectation:** Per-asset loadings from config should be the source of truth.
- **Impact:** P2 — API/contract smell rather than a statistical deviation.

#### GAP-C3 Idiosyncratic volatility is flat / asset-specific drift panel is crude
- **Lines:** 347-355
- **Current behavior:** Idiosyncratic variance is hardcoded per ticker category
  (`SYNTH=0.002^2`, `BENCH=0.001^2`, etc.) and then increased by `idiosyncratic_vol_shrink**2`.
  `shrink` is added in variance units then treated like a vol shrink, which conflates levels.
- **Institutional expectation:** Idiosyncratic vol should be regime-varying (higher in stress)
  and estimated from residuals. The "shrink" parameter is confusing and mis-calibrated:
  `+ 0.02**2` adds ~0.04% annualized variance irrespective of base idiosyncrasy.
- **Impact:** P2 — Mis-calibrated but not catastrophic; cleanup recommended.

#### GAP-C4 Correlation stability ignores regime switching
- **Lines:** 108-116 `FACTOR_CORRELATIONS`
- **Current behavior:** One static correlation matrix. In crisis regimes, correlations
  typically spike toward 1 with factor covariance increasing.
- **Institutional expectation:** Stress-correlation scaling: multiply factor correlations by
  `1 + lambda_stress * indicator(high_vol_regime)` so spreads widen in stress tests.
- **Impact:** P1 — Break-tests for tail hedging/margining underestimate worst-case correlation.

#### GAP-C5 Cholesky sequence produces correlated shocks but only uses one vector per asset loop
- **Lines:** 275-297
- **Current behavior:** `shocks = cholesky_lower @ shocks` produces correlated joint draws,
  but in the asset loop only `shocks[idx]` is used. For paths with regime-conditional variance
  absent, this results in correlation fingerprints but without time-varying vol.
- **Institutional expectation:** Combined with stochastic vol simulation, shocks should
  incorporate the current regime's covariance at every step.
- **Impact:** P1 — Methodology is incomplete without GARCH simulation.

---

## 2. Prioritized Fix Plan

### Block A — Hours 0-4: Highest Impact Realism (P0)
These fixes change the DGP in ways that directly affect break-test validity.

**A1. Wire regime parameters into `generate_path`.**  
*File:* `app/break_test/synthetic_market.py`  
*Target lines:* 239-251  
*Change:* Use `regime.mu_annual`, `regime.vol_annual`, and stochastic variance. Draw
`eps_t ~ bootstrap_residuals(size)`. Return paths whose AR, kurtosis, and tail-return frequencies
vary by regime.

**A2. Implement stochastic volatility simulation in `generate_path`.**  
*File:* `app/break_test/synthetic_market.py`  
*Target lines:* 138-168, add a new `_simulate_garch_path` helper  
*Change:* Loop over `steps` and update variance with fitted `omega/alpha/beta`.
Use stochastic variance to scale next-step innovations.

**A3. Use normalized residual bootstrap for innovations.**  
*File:* `app/break_test/synthetic_market.py`  
*Target lines:* 366-376; call site in `generate_path` and `generate_correlated_gbm_paths`
*Change:* Replace `rng.normal(...)` with `draw_standardized_residuals(size, rng)`.
Fallback to Student-t with ~5 df if residual count < 30.

**A4. Implement full factor loading usage in `_build_asset_factor_covariance`.**  
*File:* `app/break_test/synthetic_market.py`  
*Target lines:* 320-358  
*Change:* Replace hardcoded single-factor market exposure with
`FACTOR_LOADINGS[ticker]` from the local dict; retain fallback for unknowns.

**A5. Add volume simulation module.**  
*File:* `app/break_test/synthetic_market.py` + new `app/exchange/volume_simulator.py`
*Target:* New class `VolumeSimulator` producing regime-conditional clustered volume;
  export `volume: np.ndarray` alongside price path.
*Change:* Hawkes/ACD simplified: `V_t = alpha * sum(A*exp(-beta*dt)) + baseline * regime_multiplier * abs(return_t)`.

### Block B — Hours 4-8: Regime Consistency & Correlation Realism (P1)

**B1. Add regime persistence (duration) and transition matrix.**  
*File:* `app/break_test/synthetic_market.py`  
*Target lines:* 170-186 `_build_regimes`; new `_sample_regime_sequence(length, seed)`
*Change:* Average regime durations calibrated from empirical datasets (e.g., 20-60 days),
with transition probs `P_{ij}` estimated from fitted Markov chain.

**B2. Add jump arrival and size simulation.**  
*File:* `app/break_test/synthetic_market.py`  
*Target lines:* 24-36 `RegimeSpec`; new `_inject_jumps(returns, regime)`
*Change:* Poisson draws with rate `lambda_jump * (1/252)`; size drawn from
`N(mu_jump, sigma_jump^2)`; replace index-`t` return with sum.

**B3. Stress-correlation scaling.**  
*File:* `app/break_test/synthetic_market.py`  
*Target lines:* 108-116 `FACTOR_CORRELATIONS`, 342 factor_cov
*Change:* Add `stress_correlation_multiplier: float = 0.35` parameter.
When `regime_key in {"high_volatility", "sudden_selloff"}`, scale factor cov by `1 + lambda`.

**B4. Volatility mean-reversion and leverage feedback (basic EGARCH/GJR term).**  
*File:* `app/break_test/synthetic_market.py`  
*Target lines:* 138-168  
*Change:* Add `gamma` leverage parameter to variance update when fitting future
extension; for now expose it as a regime-dependent modifier.

**B5. Volume-pressure VWAP/LOB depth reactivity.**  
*File:* `app/exchange/volume_profile.py`  
*Target lines:* 37-51 `displayed_depth_autor`  
*Change:* Add per-step depth series `depth[t] = base_depth * exp(-eta * abs(return_t)) * regime_mult`,
  correcting for liquidity crises. Expose `depth_series` along with volume.

### Block C — Hours 8-12: Robustness, Diagnostics, Calibration Guardrails (P1-P2)

**C1. Convergence diagnostics for GARCH fit.**  
*File:* `app/break_test/synthetic_market.py`  
*Target lines:* 138-158  
*Change:* Log log-likelihood each iteration; break if `abs(delta_LL) < 1e-6`.
Warn and fall back to sensible defaults if `persistence > 0.995` (near-integrated).

**C2. Regime parameter sanity/unit tests.**  
*File:* `app/break_test/synthetic_market.py`  
*Target lines:* 170-186  
*Change:* Add assertions that `vol_annual > 0`, persistence in `(eps, 1-eps)`.
Test that simulated paths have realized volatility within 30% of target in-sample.

**C3. Fix idiosyncratic vol shrink semantics.**  
*File:* `app/break_test/synthetic_market.py`  
*Target lines:* 344-356  
*Change:* Rename to `idiosyncratic_vol_bps` and add in vol space (not variance space).
Expose per-regime scales.

**C4. Fix length/step semantics across path generators.**  
*File:* `app/break_test/synthetic_market.py`  
*Target lines:* 239, 253, 286  
*Change:* Enforce `steps = int(length)` with defensive min/max matching caller expectations.

**C5. Add path-level diagnostics output to generator report.**  
*File:* `app/break_test/synthetic_market.py`  
*Target lines:* 245-251, 307-318  
*Change:* Include `realized_vol`, `skewness`, `kurtosis`, `jump_count`,
`avg_correlation` in returned dicts. Enables downstream fidelity dashboards.

### Block D — Hours 8-12 (parallel): Volume realism without price-model rewrite

**D1. Dynamic intraday volume weights.**  
*File:* `app/exchange/volume_profile.py`  
*Target lines:* 9-34  
*Change:* Add randomization on U-shape amplitude; add `morning_session_bias` and
`afternoon_session_bias` scalars parameterized by regime.

**D2. Volume autocorrelation/clustering.**  
*File:* new `app/exchange/volume_simulator.py`  
*Change:* Add `VolumeSimulator.generate(regime, returns, seed) -> np.ndarray` using
exponential decay kernel similar to Hawkes. Baseline levels per `liquidity_profile`.

---

## 3. 24-Hour Stretch Goal

**Stretch-S1. Coupled stochastic volatility + regime-switching + Hawkes volume + LOB depth.**  
Integrate a 3-regime Markov switching GARCH-like simulator that uses the fitted parameters
per regime, samples transitions, injects jumps, clusters volume via Hawkes, and feeds
volume and volatility shocks into a simple LOB depth model.

**Stretch-S2. Cross-asset multi-asset-class extension.**  
Extend `FACTOR_CORRELATIONS` to include rates/credit/commodity factors; accommodate
carry/funding-rate dynamics. This requires adding `rates`, `credit`, `commodity` factor buckets
and associating them with new asset categories.

**Stretch-S3. Fidelity metric suite.**  
Add `synthetic_market_fidelity.py` comparing empirical moments of synthetic vs reference
datasets (auto-correlation of absolute returns, realized vol, volume autocorrelation,
correlation stability across sub-periods). Gate generator commits on regression tests.

---

## 4. Implementation Checklist (12-hour sprint)

| # | Hours | Target File | Change | Priority |
|---|-------|-------------|--------|----------|
| A1 | 0-0.5 | `synthetic_market.py` | regime -> mean/var mapping in `generate_path` | P0 |
| A2 | 0.5-2 | `synthetic_market.py` | stochastic GARCH simulation helper | P0 |
| A3 | 2-2.5 | `synthetic_market.py` | residual bootstrap in generators | P0 |
| A4 | 2.5-3 | `synthetic_market.py` | full factor loading usage | P0 |
| A5 | 3-4 | new `volume_simulator.py` | basic dynamic volume | P0 |
| B1 | 4-5 | `synthetic_market.py` | regime duration + transition sampling | P1 |
| B2 | 5-6 | `synthetic_market.py` | Poisson jump injection | P1 |
| B3 | 6-7 | `synthetic_market.py` | stress-correlation scaling | P1 |
| B4 | 7-7.5 | `synthetic_market.py` | leverage term in variance | P2 |
| B5 | 7.5-8 | `volume_profile.py` | LOB depth decay | P1 |
| C1 | 8-8.5 | `synthetic_market.py` | GARCH fit convergence diagnostics | P1 |
| C2 | 8.5-9 | `synthetic_market.py` | sanity assertions + tests | P1 |
| C3 | 9-9.5 | `synthetic_market.py` | idiovol semantics fix | P2 |
| C4 | 9.5-10 | `synthetic_market.py` | length/step semantics fix | P2 |
| C5 | 10-10.5 | `synthetic_market.py` | path diagnostic enrichments | P2 |
| D1 | 10.5-11 | `volume_profile.py` | randomized intraday weights | P2 |
| D2 | 11-12 | new `volume_simulator.py` | volume clustering | P1 |

---

## 5. Verification Protocol

1. **Moment matching:** For each regime, synthetic paths must match target annualized vol within
   ±20%, skew within ±0.3, excess kurtosis > 0, jump rate within 2x target.
2. **Correlation checks:** Correlation matrix of generated paths must be positive semi-definite;
   average pairwise corr within ±0.15 of target factor model.
3. **Persistence checks:** Autocorrelation at lag 1 of absolute returns > 0.05;
   autocorrelation of volume > 0.3.
4. **Break-test non-regression:** Re-run `demo.py` and `scripts/benchmark_baseline.py` to ensure
   PnL distributions shift in expected directions by regime (higher vol regimes show higher DD).

---

*Audit generated for project:* `/Users/scottthomasswitzer/Documents/OAI_Build_Week`  
*Primary file under review:* `app/break_test/synthetic_market.py`  
*Supporting file:* `app/exchange/volume_profile.py`  
*Consumers:* `app/break_test/regimes.py`, `demo.py`, `scripts/benchmark_baseline.py`
