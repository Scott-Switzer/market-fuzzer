# Calibration data boundary

Historical data calibrates declared generator behavior. It is never a sealed
evaluation world, and no generated path is represented as a historical replay.

## Source-manifest contract

Every authorized calibration import creates a `CalibrationDataManifestV1` with
a source identifier, SHA-256 checksum, rights basis, maximum data resolution,
transient row count, disjoint calibration and held-out intervals, supported
properties, and prohibited claims. Raw source rows are not retained in packs or
evidence reports.

| Resolution | Permitted examples | Prohibited without higher-resolution evidence |
| --- | --- | --- |
| OHLCV | returns, volatility, volume, intraday seasonality | queue position, fill probability, cancellation behavior |
| Trades | arrival/size and short-horizon response | queue position, fill probability, cancellation behavior |
| BBO | quoted spread and top-of-book depth | queue position, fill probability, cancellation behavior |
| MBP | displayed depth, imbalance, depth dynamics | individual queue-order claims |
| MBO | order arrival, cancellations, queue dynamics | universal venue fill claims |
| Fundamentals, macro, news | cross-sectional and exogenous regimes | order-book and fill mechanics |

`generated_world_similarity` compares a generated trajectory with in-memory
reference-price windows. It returns only checksums and aggregate similarity
statistics. Exact return-window duplication or near-perfect correlation raises
a warning. This is evidence against copying, not a mathematical proof of
novelty.

## Portfolio Engine inventory (read-only, 2026-07-19)

Inspection was restricted to source code, schemas, adapters, documentation, and
`.env.example` under `/Users/scottthomasswitzer/Documents/Financial System/portfolio-engine`.
No actual `.env`, credentials, raw datasets, databases, logs, or paid API
responses were read.

| Surface | Potential category | Permitted use here | Status |
| --- | --- | --- | --- |
| `price_1d`, `price_intraday_bar` providers | OHLCV / bars | return, volatility, volume, and seasonal aggregates | needs rights manifest |
| SEC EDGAR / SimFin / FMP | fundamentals | cross-sectional and event regimes | point-in-time and rights checks required |
| FRED/ALFRED, Kenneth French, EIA | macro/factors | macro and cross-asset regime schedules | vintage/rights metadata required |
| News providers and event datasets | news events | exogenous event-regime calibration | lineage and rights required |
| Identifier/master-data sources | identifiers | instrument normalization only | not market-mechanics calibration |

The inspected registry documents no order-by-order MBO adapter or validated
market-by-price feed for this platform. Price tables therefore begin as OHLCV
unless a source manifest proves higher resolution. Its registry also says no
source is approved for execution pricing; this platform preserves that boundary.

## Acceptance evidence before persistence

1. Rights basis and maximum resolution are declared.
2. Calibration and held-out periods are disjoint and chronological.
3. Source checksum is recorded; rows remain outside public artifacts.
4. Resolution-incompatible claims are rejected.
5. Generated worlds are compared with untouched reference windows.
6. Public development and sealed evaluation worlds are not calibrated on their
   own evaluation trajectories.
