# Transaction-Cost Model Design Notes

## Purpose
Replace the legacy flat 2 bps turnover assumption with a price-, volume-, and volatility-dependent
execution cost model that can be configured per asset class through `ExchangeSpec`.

## Design Principles
- **Almgren-Chriss (2000)** decomposition: temporary + permanent impact.
- **Backward compatible**: when new `ExchangeSpec` fields are absent, behavior falls back to the
  old flat 2 bps turnover assumption.
- **Deterministic**: no hidden randomness; identical inputs → identical cost output.
- **Unit isolation**: all formulations operate on *relative notional* and produce bps (basis points).

## References
- T. Almgren & N. Chriss, “Optimal Execution of Portfolio Transactions,” *J. Risk*, 2000.
- R. Almgren et al., “Equity Market Impact,” *Risk*, 2005.
- A. Madhavan, M. Richardson & M. Roomans, “Why Do Security Prices Change? A Transaction-Level
  Analysis of NYSE Stocks,” *Rev. Fin. Stud.*, 1997.
- A. Perold, “The Implementation Shortfall,” *J. Portf. Mgmt.*, 1988.
- SEC Rule 606 (broker-execution quality disclosure).
- Lehmann (2004) liquidity multiplier / volume-surge modeling for synthetic worlds.

## Formulas

### 1. Market Impact  (Almgren-Chriss, simplified)

Let:
- `x = Q / ADTV`  (relative order size)
- `σ_d = daily_vol` (daily return standard deviation)
- `η, ε, γ` = configurable coefficients (`perm_eta`, `temp_epsilon`, `temp_gamma`)

```
permanent_bps = η · x · σ_d · 10,000
temp_bps      = (ε + γ · √x) · σ_d · 10,000
```

**Properties:**
- Permanent impact is *linear* in relative size (one-way information effect on mid-price).
- Temporary impact has a *linear* shortfall component (liquidity urgency) plus a
  *square-root* component (convex execution urgency); both scale with daily volatility.
- Coefficients have domain `[0, 1]`, `[0, 1]`, `[0, 5]` respectively, with defaults calibrated so
  a 0.1 % of ADTV order in a stock with σ_d = 1.5 % produces ~2 bps total cost ≈ the old 2 bps
  default.

### 2. Exchange Fees
Two modes:
1. **Tiered schedule** (preferred): `maker_fee_schedule` / `taker_fee_schedule` ordered by
   cumulative notional.
2. **Flat fallback**: `maker_fee_bps` / `taker_fee_bps` in `ExchangeSpec`.

Tier lookup:
```
fee_bps = lookup_tier(cumulative_notional_cents, schedule) or flat_fee_bps
```

Rebates (maker rebates are expressed as negative `maker_fee_bps`) are preserved unchanged.

### 3. Borrow Fees  (short locates / HB)
```
annual_borrow_fee_decimal = (locate_fee_bps_annual + htb_bps_annual) / 10,000
short_cost_bps_per_trade  = annual_borrow_fee_decimal * (holding_days / 365) * 10,000
                    = (locate_fee_bps_annual + htb_bps_annual) * holding_days / 365
```

Charged against any bar where short inventory > 0 and rolling bid-ask pressure is available;
default holding_days=1 for synthetic backtests.

### 4. Spread
Half the standardized quoted spread is approximated as:
```
spread_bps = clamp(σ_d · 5 000, hi=200)
```
This is a conservative surrogate proportional to realized volatility, consistent with
Handa & Schwartz (1996) liquidity proportions.

### 5. Total Cost
```
total_bps = spread_bps + temporary_impact + permanent_impact + fee_bps + borrow_cost_bps
```

## Backward Compatibility
- All new `ExchangeSpec` fields have defaults.
- `backtest_metrics` accepts `exchange_spec: ExchangeSpec | None`. When `None`, falls back to the
  legacy flat `fee_bps` parameter.
- Callers outside `app/break_test/` that pass only `fee_bps=2.0` continue to work unchanged.

## File Map
```
app/break_test/cost_model.py   <-- formulas + CostInputs / CostModelResult
app/schemas/world.py           <-- new ExchangeSpec fields
app/break_test/metrics.py      <-- replacement for fee_bps hardcode
app/robustness_product.py      <-- inline 2 bps → cost model
app/break_test/quant_validation.py  <-- inline 2 bps → cost model
app/simulation.py              <-- summary extensions + exchange-fee model
app/break_test/COST_MODEL_NOTES.md <-- this file
```
