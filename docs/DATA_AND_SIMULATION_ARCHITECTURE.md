# Data and simulation architecture

Synthetic Market World is a counterfactual strategy-validation product. Its
core question is: “When this strategy is placed inside a declared exchange and
the market is stressed, which behaviors fail, and which remain controlled?”
Local market data improves the calibration of the world; it does not turn a
synthetic result into a live-market forecast.

## Evidence tiers

| Data available | Use in this product | Claim supported |
| --- | --- | --- |
| OHLCV bars | Realized volatility, volume scale, time-of-day and regime aggregates | Aggregate calibration and scenario parameter ranges |
| Trades / BBO | Event timing, spread and short-horizon response checks | Stronger event-level calibration |
| MBP / MBO order events | Queue, displayed depth, order arrival, cancel and fill mechanics | Microstructure-aware exchange calibration |
| Synthetic interventions | Counterfactual liquidity withdrawal, latency, volatility and forced flow | Strategy behavior under declared stress |

The local-data adapter currently consumes intraday OHLCV Parquet and derives
explicit spread, depth, and signed-flow proxies. It hashes the source file,
retains aggregate train/validation/test windows, and never persists source
rows. It must not be described as queue-level calibration.

## Local data path

Build an inspectable aggregate pack from a local intraday source:

```bash
./.venv/bin/python scripts/build_local_calibration_pack.py \
  /path/to/price_intraday_bar.parquet \
  --timeframe 1Min \
  --pack-id local-polygon-2024-proxy-v1 \
  --instrument SPY \
  --venue polygon-observed \
  --output /tmp/local-polygon-2024-proxy-v1.json
```

Attach the JSON pack to a declared world through
`POST /api/enterprise/worlds/{world_id}/calibration`, or upload a canonical CSV
through the World Registry. Data licensing and authorization remain the
operator's responsibility; raw market files are intentionally not copied into
this repository or Docker image.

## External references and design choices

- [Databento schemas](https://databento.com/docs/schemas-and-data-formats/whats-a-schema)
  distinguishes OHLCV from BBO, MBP, and MBO. We use that vocabulary for the
  import boundary and reserve MBO/MBP claims for a future order-event adapter.
- [ABIDES](https://github.com/abides-sim/abides/wiki) demonstrates why an
  agent-based discrete-event architecture is useful for interactive market
  experiments. We borrow the architectural idea, not implementation code.
- [hftbacktest](https://hft.readthedocs.io/en/latest/) is a useful replay and
  order-book comparator. Its [fill-model documentation](https://hft.readthedocs.io/en/latest/order_fill.html)
  also makes the important limitation clear: replay does not change the market
  and therefore does not model market impact. That is why this product keeps a
  controlled synthetic exchange for counterfactual intervention tests.
- [kdb+ tick](https://code.kx.com/q/learn/startingkdb/tick/) is a reference
  pattern for capturing and querying high-volume time series. We do not add a
  kdb dependency for the submission; the current adapter is intentionally
  Parquet-based and portable.

## Strategy boundary

Plain English is compiled into a reviewable allow-listed policy proposal. Code
is not uploaded or executed by the API. Customer-owned executable logic uses
`http_json_v1`: the service sends `strategy_observation_v1` to an operator-
allowlisted HTTP endpoint and validates the returned `execution_action_v1`.
Timeout, endpoint host, contract hash, and adapter provenance are recorded with
the result.

The synthetic exchange remains authoritative for order admission, matching,
latency, interventions, fills, and metrics. This separation is what makes the
result useful for stress testing without pretending that an aggregate historical
tape is a complete replica of a live venue.
