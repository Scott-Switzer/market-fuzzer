# Universe CSV Schema

Use this schema when supplying an offline universe CSV via `universe_csv=` in
`build_world()` or `run_exchange_forward_test()`.

## Basic row format

```csv
ticker,company_name,sector,initial_price_ticks,shares_outstanding,initial_fundamental_value_ticks,macro_beta,idiosyncratic_volatility,liquidity_profile,event_sensitivity,mean_reversion,price_cache_factor_loading
SYNTH,Synthetic Asset,Synthetic,10000,50000000,10000,1.0,0.002,deep,1.0,0.02,1.0
XLK,Technology Select Sector,Technology,18000,80000000,18000,0.78,0.0018,deep,1.05,0.018,0.76
RATES,Rates Proxy,Macro/Rates,12000,100000000,12000,0.22,0.0012,deep,0.85,0.03,0.35
```

## Field descriptions

| Field | Type | Notes |
|---|---|---|
| `ticker` | string | Upper-case ticker or symbol used as the asset key. |
| `company_name` | string | Display name. |
| `sector` | string | Used for factor/sector classification and downstream reporting. |
| `initial_price_ticks` | int | Starting simulated price. |
| `shares_outstanding` | int | Used for market-cap / liquidity heuristics. |
| `initial_fundamental_value_ticks` | int | Fundamental anchor for fundamentalist agents. |
| `macro_beta` | float | First-factor exposure. Used as market beta and as a fallback when explicit `price_cache_factor_loading` is not supplied. |
| `idiosyncratic_volatility` | float | Square root of the diagonal idiosyncratic variance in the annualized covariance matrix. |
| `liquidity_profile` | enum | `deep`, `normal`, or `thin`. |
| `event_sensitivity` | float | Multiplier applied to regime/jump shocks. |
| `mean_reversion` | float | Mean-reversion speed used by mean-reversion agents. |
| `price_cache_factor_loading` | float | Optional. Legacy correlation/scalar exposure for the old single-factor cached-path generator. For the new factor-Cholesky generator, prefer providing `macro_beta`; explicit factor loadings beyond the first factor can be added as extra comma-separated columns. |

## Extended factor-loading columns

Optionally append extra columns after `price_cache_factor_loading` to encode
per-asset exposures to secondary factors. When present, the parser reads extra
fields in order and assigns them as additional factor loadings in
`ResearchSyntheticMarketGenerator.generate_correlated_gbm_paths()`.

```csv
ticker,...,price_cache_factor_loading,value_loading,momentum_loading,size_loading,rates_loading
SYNTH,...,0.35,0.10,0.15,-0.05,-0.35
```

If fewer factor columns are supplied than the generator's active factor universe,
missing loadings default to `0.0`.

## File rules

- The first non-comment, non-header line determines the parser's column count.
- Lines beginning with `#` are ignored.
- Empty lines are ignored.
- If fewer rows than `asset_count` are provided, remaining slots are filled
  with auto-generated synthetic assets.
