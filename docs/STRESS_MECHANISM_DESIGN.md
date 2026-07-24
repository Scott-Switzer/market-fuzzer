# Stress Mechanism Design & Fixes (Subagent B)

Scope: `app/strategy_lab/submission/stress_search.py`. Every mechanism must be **economically
meaningful**, **continuously intensity-controlled** (monotone), **cross-process deterministic**,
**confirmed** (repeated seeds + predicate), **minimized**, and **honestly named**. `intensity ∈ [0.5,1.5]`.

## 0. Global determinism (line 61 bug)
`hash(mechanism)` is salted per-process → non-reproducible worlds. Fix:
```
import hashlib
def _mech_seed(mechanism, seed):
    h = int.from_bytes(hashlib.sha256(mechanism.encode()).digest()[:8],'big')
    return np.random.default_rng((seed ^ h) & 0xFFFFFFFFFFFFFFFF)
```
Test: `_mech_seed('x',1)` identical across 2 subprocesses; different mechanisms → different streams.

## 1. momentum_reversal (OK, tighten)
Keep late mean-reversion. Confirm monotone: reversion magnitude ∝ intensity. Test: sharpe(1.4) ≤ sharpe(0.6).

## 2. volatility_expansion (OK)
`out *= 1 + N(0, intensity·0.02)`. Monotone in std. Test: realized vol of returns increases with intensity.

## 3. correlation_breakdown (BUG: column permute = identity swap, ignores intensity)
Permuting columns just relabels assets — no correlation change, non-deterministic identity scramble.
Replace with **return-space blending** toward a target low-correlation structure on the 2nd half,
preserving each asset's own marginal path identity:
```
r = log-returns of 2nd half         # (H,N)
mu, sd = r.mean(0), r.std(0)
z = (r-mu)/sd                        # standardized
# orthogonal (independent) surrogate with SAME marginals via per-column shuffle of z rows
z_indep = column-wise row-permutation of z (rng)
alpha = clip((intensity-0.5), 0, 1) # 0 at low, →1 at high
z_new = sqrt(1-alpha)*z + sqrt(alpha)*z_indep   # variance-preserving blend
r_new = z_new*sd + mu               # marginals (vol, drift) preserved
rebuild prices: close2[t]=close2[t-1]*exp(r_new[t]); splice onto 1st half
```
Monotonicity proof sketch: for jointly-normal cols, Corr(z_new_i,z_new_j)=(1-alpha)·ρ_ij (independent
surrogate contributes 0 cross-corr, variance normalized). So mean |offdiag corr| = (1-alpha)·base,
strictly decreasing in alpha, i.e. in intensity. Identity/marginal vol preserved ⇒ continuity.
Confirm: measured mean pairwise corr of 2nd-half returns strictly ↓ as intensity ↑.
Test: corr(intensity=1.4) < corr(intensity=0.6) on same seed.

## 4. volatility_compression (BUG: adds noise, ignores intensity)
Current `*(1+N(0,0.002))` ADDS variance — wrong sign, no intensity. Compression must **scale
deviations from a local trend below 1**:
```
r = returns (2nd half); mu = r.mean(0)
factor = clip(1.5 - intensity, 0.0, 1.0)   # intensity 0.5→1.0 (mild), 1.5→0.0 (full crush)
r_c = mu + factor*(r-mu)                    # shrink dispersion toward mean, keep drift
rebuild prices from r_c
```
Monotonicity: std(r_c)=factor·std(r); factor strictly ↓ in intensity ⇒ realized vol strictly ↓.
Economic meaning: vol-crush regime starves low-vol/mean-reversion edge and compresses cross-sectional spread.
Confirm/Test: realized vol(1.4) < vol(0.6); factor∈[0,1] so never expands.

## 5. spread_inflation / 6. slippage_inflation / 7. borrow_cost_increase (OK — engine-level)
Handled in `_effective_spec`; multiplicative in intensity, monotone. Keep. Test: cost_pct ↑ with intensity.

## 8. short_unavailability (BUG: lowers ALL prices — that's a market crash, misnamed)
Should make **selected assets non-shortable**, forcing the strategy to hold/skip shorts, not move prices.
Requires eligibility signalling. Design: add `non_shortable: tuple[str,...]` to `AssetMetadata`/panel
(engine consumes: clamp target weight ≥ 0 for flagged names, redistribute freed gross to remaining shorts).
Intensity → fraction of universe made non-shortable:
```
k = round(clip(intensity-0.5,0,1) * N)      # 0..N assets
flagged = rng.choice(N, size=k, replace=False)
mark metadata[flagged].non_shortable=True    # NO price change
```
Monotonicity: more banned shorts ⇒ weakly less short alpha capture / larger tracking error, monotone in k(intensity).
Confirm: short book gross of flagged names == 0 in engine output. Until engine supports flag, name honestly
as `short_ban` and record as **not-yet-evaluated** rather than faking via price drops.
Test: k(1.5)=N, k(0.5)=0, monotone; flagged short weights zeroed.

## 9. delayed_rebalance (BUG: no-op; comment lies)
Implement a **D-day execution delay**: target weights computed at rebalance t are applied at t+D.
Engine-level (preferred): shift `active_target` forward by D rows before the fill loop:
```
D = int(round(intensity*2))   # 1..3 trading days
active_target_delayed[t] = active_target[t-D]  (t<D → flat/zeros)
```
Monotonicity: staleness grows with D; in a drifting/reverting tape, tracking error and cost of
late entry increase weakly-monotonically in D(intensity). Confirm: turnover timing shifted by exactly D;
returns differ from D=0 baseline. Test: mean |weight-lag| increases with intensity; D≥1 always.

## 10. universe_churn (BUG: freezes a price ≠ removing eligibility)
Freezing a price fabricates a zero-vol asset (spurious low-vol pick). Real churn = **delisting/eligibility
removal**: asset exits the tradable set mid-panel; engine must force-liquidate & exclude from ranking.
Design: add `eligible: np.ndarray (T,N) bool` to panel (or NaN-close AFTER a documented liquidation date
that the engine treats as "exit at last valid price, weight→0 thereafter"). Intensity → number of assets churned:
```
k = 1 + round(clip(intensity-0.5,0,1)*(N-2))
for a in rng.choice(N,k): eligible[T//2:, a] = False
```
Monotonicity: more removals shrink the opportunity set ⇒ weakly worse diversification/sharpe, monotone in k.
Confirm: engine reports 0 weight & realized liquidation trade for churned names post-exit.
Test: k monotone in intensity; churned-asset post-exit weight == 0.

## 11. missing_data_shock (BUG: silent forward-fill hides the gap; policy implicit)
Keep NaN injection but make the **fill policy explicit and declared**, and scale gap length with intensity:
```
gap = int(round(5 + 10*(intensity-0.5)))    # 5..15 days
col = rng.integers(0,N); out[t0:t0+gap,col]=nan
policy = "forward_fill" | "drop_from_universe" | "halt_trading"  # RECORD in provenance.transformations
```
Report the chosen policy per world; forward-fill must be an explicit choice, not a hidden `_fill_nan`.
Monotonicity: longer stale window ⇒ larger mispricing/return jump on reconnect, weakly monotone in intensity.
Confirm: gap length == declared; policy string present in provenance.
Test: gap(1.4) > gap(0.6); provenance contains policy tag.

## 12. mechanisms_searched honesty (BUG: returns full registry)
`run_fast_search` returns `STRESS_MECHANISMS` (all 11) regardless of what ran under `budget`.
Fix: track a `set()` of mechanisms that actually completed a backtest (post try/except success):
```
evaluated_mechs.add(w.mechanism)  # only on successful run, not engine_error
...
"mechanisms_searched": sorted(evaluated_mechs),
"mechanisms_registry": STRESS_MECHANISMS,
```
Also `evaluated` should count successful evals, not attempts; add `attempted` and `engine_errors`.
Test: with budget=3, len(mechanisms_searched) ≤ 3 and ⊆ registry; engine-errored mechs excluded.

## Cross-cutting confirmation rule
A failure is CONFIRMED only if: (a) ≥2 seeds, same mechanism, same predicate violated; (b) monotone —
violation persists/worsens at higher intensity; (c) data valid (no NaN post-policy); (d) no engine error.
Minimization: bisect intensity down to the smallest violating value; record nearest passing world.

## Monotonicity test harness (generic sketch)
```
def assert_monotone(mech, metric_fn, worse_is='down'):
    lo = metric_fn(apply_mechanism(base, mech, 0.6, S))
    hi = metric_fn(apply_mechanism(base, mech, 1.4, S))
    assert (hi < lo) if worse_is=='down' else (hi > lo)
# vol_expansion: worse_is='up'(vol); compression:'down'(vol);
# corr_breakdown:'down'(mean corr); churn/short_ban/delay: sharpe 'down'
```
Determinism test: run apply_mechanism in a subprocess, assert byte-equal arrays.
