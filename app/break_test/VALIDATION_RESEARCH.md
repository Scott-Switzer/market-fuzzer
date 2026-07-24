# Break-Test Validation Research Notes

This document records the production validation methodology implemented in
`app/break_test/oos_validation.py` and the probabilistic statistics backing it.
The goal is to keep the algorithmic intent explicit so future maintainers can
update formulas without changing documented behavior.

## 1. Walk-Forward Validation

### 1.1 Train/Test Expansion
Instead of a single fixed train/test cut, we slide a window across the price
series. Each fold uses:
- `train_start = 0` when anchored, otherwise `max(0, start - train_window)`
- `train_end = start`
- `test_start = start + embargo`
- `test_end = test_start + effective_test`

The `effective_test` resolves from `test_window`, then `step`, then
`train_window`. `effective_step` defaults to `effective_test` so folds tile
without overlap when unspecified.

### 1.2 Embargo
After each training segment we insert an embargo gap of `embargo` bars before
the first test observation. This reduces leakage from overlapping labels or
autocorrelated returns.

### 1.3 Anchored vs Rolling
- Anchored: training always starts at bar 0. Older information never drops out.
- Rolling: training slides forward with window length `train_window`.

Both modes return the same fold count when inputs are otherwise identical.

## 2. Regime-Aware Similarity Scoring

We compute a compact feature vector for each fold's test segment using recent
log returns:
- vol_annual / 0.5
- drift_annual / 0.5
- fraction of high-volatility periods / 50

Cosine similarity between the current market's feature vector and each fold's
vector is calculated. Similarities are normalized to sum to one, producing
per-fold regime weights. These weights flow into the summary as `weighted_oos_sharpe`
and `regime_weights`.

## 3. Probabilistic Sharpe Ratio (PSR)

Reference: Bailey & López de Prado (2014), *Breadth-first search for Sharpe
ratio spaces*.

PSR tests whether the true Sharpe ratio exceeds a target `SR*`, here approximated
relative to zero:

```
z = Sharpe * sqrt(N)
denom = sqrt(max(eps, 1 - Sharpe^2 * (N - 1) / N))
adjusted_z = z * denom
PSR = CDF(adjusted_z)
```

The returned `psr_vs_zero` is between 0 and 1. Values nearer 1 indicate stronger
evidence that the strategy has positive expected return per unit risk.

## 4. Deflated Sharpe Ratio (DSR)

Reference: Bailey et al. (2014), *The Deflated Sharpe Ratio*.

When selecting the best strategy or best fold from `k` trials, the observed
Sharpe must be deflated to account for selection bias. We approximate the
lower-bound deflated Sharpe via a clip-normalized heuristic:

```
z_score = SR / sqrt(max(eps, SR^2 - 1/(k - 1)))
percentile = CDF(z_score)
shrunk_mean = SR * sqrt(1 + 1/(k - 1))
lower_bound = shrunk_mean * percentile - sqrt(1/(k - 1)) * CDF(z_score - 1.96/sqrt(k))
DSR = clip(lower_bound, -5, 5)
```

This returns a conservative lower bound rather than an exact DSR probability.
The value is interpreted as a deflated Sharpe point estimate.

## 5. Consistency Sharpe Ratio (CSR)

We define consistency Sharpe as Sharpe divided by fold-standard deviation,
penalized by an exponential based on mean performance, and scaled to annualized
Sharpe units:

```
CSR = mean(SR_i) / std(SR_i) * exp(-mean(SR_i)) * sqrt(252)
```

High CSR indicates consistently positive Skewness-adjusted performance.

## 6. Combinatorial Purged Cross Validation (CPCV)

Reference: Marcos López de Prado (2018), *Advances in Financial ML*, Chapter 7.

Rather than fixed K-fold, we:
1. Split the series into `blocks` contiguous segments.
2. Enumerate combinations of blocks used as train or test baskets.
3. Apply an embargo gap around each test block when masking the training set.
4. Evaluate each combination as an independent OOS fold.
5. Summarize with the same Sharpe point-estimate and probabilistic metrics as
   walk-forward validation.

CPCV produces an empirical distribution of OOS Sharpe ratios, making it possible
to assess stability rather than a single holdout estimate.

## 7. Implementation Constraints

- We intentionally reuse `backtest_metrics` and `compute_positions` to avoid
  changing historical behavior or breaking imports used by FastAPI routes in
  `app/api/app.py`.
- `detect_regimes` is imported from `app/break_test.regimes`, which depends on
  `ResearchSyntheticMarketGenerator`. We tested that import path at module
  load and it succeeds.
- `walk_forward_validation` is called from `/api/quant/oos`. The request body
  does not currently pass `anchored` or `regime_aware`, so defaults remain
  backwards-compatible.

## 8. Testing Strategy

Added `tests/test_oos_validation.py` covering:
- default walk-forward produces folds
- anchored vs rolling mode flagging
- regime-aware weights sum to one
- insufficient-data empty result behavior
- return-value sanity for deflated Sharpe and PSR range
- CPCV precondition behavior

Regression tests in `tests/test_quant_validation.py` are untouched and should
still pass under the venv.
