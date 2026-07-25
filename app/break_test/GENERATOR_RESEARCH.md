# Research-Grade Synthetic Market Generator — Implementation Notes

## Summary
Replaced the handmade regime constants in `app/break_test/regimes.py` with a
calibrated generator engine.  The new defaults still honor the legacy public
interface (`build_world_price_path`, `detect_regimes`, `_REGIME_KEYS`,
`_REGIME_LABELS`, `_REGIME_SPECS`) so existing forward-test logic remains
unchanged from the caller’s perspective.

## New Module
* `app/break_test/synthetic_market.py`

### Core Pieces

1. **GARCH(1,1) fit with out-of-sample residual extraction**
   * `_fit_garch()` performs a lightweight numerical optimizer over standard
     GARCH parameters using analytical score-style updates.
   * Returns a `GARCHParams` dataclass with `omega`, `alpha`, `beta`, and
     `unconditional_vol`.
   * `_compute_standardized_residuals()` then produces standardized residuals
     for downstream bootstrap draws.

2. **Regime detector based on realized vol, GARCH unrealized vol, and tail skew**
   * `detect_regime()` examines annualized realized vol together with GARCH
     unrealized vol and an empirical tail skew measure.
   * Assigns one of four regime buckets that match the existing label strings
     expected by forward test consumers.

3. **Regime-conditional parametric path generation with Poisson jumps**
   * Each regime is a `RegimeSpec` dataclass with annual drift, volatility,
     mean-reversion speed, long-run vol target, jump intensity, jump mean/size,
     and a mean-reverting jump component.
   * `_generate_single_path()` runs a daily Euler scheme in vol² space with
     Poisson jump arrivals (Poisson draws for N, then Gaussian jumps fixed to
     daily horizon).
   * Uses the fitted GARCH standardized residuals as the innovation source for
     more realistic ARMA-like persistence (`draw_standardized_residuals`).

4. **Cross-sectional correlation through a one-factor residual model**
   * `generate_correlated_paths()` first draws shared factor innovations for the
     horizon, then idiosyncratic Gaussian noise, and linearly combines them to
     the target correlation derived from empirical vol clustering.
   * Each asset may be assigned a different regime key while preserving the
     factor contamination.

5. **Stable module-level defaults**
   * `regimes.py` instantiates a shared `ResearchSyntheticMarketGenerator()`
     and translates its lookup attributes into the legacy module-level dicts
     `_REGIME_KEYS`, `_REGIME_LABELS`, and `_REGIME_SPECS`.
   * This means `app.break_test.quant_validation._quick_forward_test` still
     compiles without behavioral changes.

## Testing
All tests in `tests/test_break_test.py` and `tests/test_generators_v1.py`
pass under the venv with `env -u PYTHONPATH`.

## Known Limitations / Future Work
* GARCH estimation currently relies on a simple local optimizer; a full MLE or
  Bayesian sampler could improve stability in high-leverage tails.
* Jump size is normally distributed; future iterations should consider a
  variance-gamma or double-exponential mixture.
* Factor model is currently one-factor + diagonal idiosyncratic; higher-order
  PCA factors are a natural extension.
* The legacy `_REGIME_SPECS` constant mapping in `regimes.py` is kept but not
  programmatically regenerated.  After sufficient validation, those values
  can be removed or replaced with programmatic equivalents.
