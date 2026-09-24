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

## World V2 semantic randomness

World V2 uses the standalone `SEMANTIC_RNG_V3` adapter in `app.world.rng`; it
does not import the private `financial-system-core` package at runtime. Each
draw is keyed by the canonical tuple `(namespace_version, world_id, entity_id,
mechanism_id, variable, distribution_id)`. Canonical JSON is hashed with
SHA-256, the first 128 bits become a little-endian NumPy Philox `2x64` key, and
the counter is `[0, period_ordinal, draw_index, 0]`. The engine consumes only
raw Philox output before applying the versioned 53-bit uniform or Box-Muller
normal transform.

The public `world_id` and `seed` remain unchanged. For address construction
only, they are encoded as the canonical JSON array `[world_id, seed]`; this is a
migration bridge, not a V3 seed field or a new namespace. Roster draws use
`ROSTER:<three-digit slot>` identities, while later draws use stable
`COMPANY:<ticker>`, `SECTOR:<sector>`, and `WORLD` identities. Static draws use
period zero, quarterly draws use the quarter index, and no mutable draw counter
is carried between calls.

The full address registry is hidden state. Public artifacts receive no registry
entries or internal world identity; the release manifest carries only the
namespace, transform-version map, and a deterministic canonical-JSON SHA-256
of the registry.

## World V2 ledger authority

World V2 separates economic simulation from accounting. Causal state may use
floating point for demand, growth, margins, macro variables, prices, and latent
variables. At the accounting boundary, `app/economy/accounting_v1.py` converts
monetary inputs with `Decimal(str(value))`, rounds once to canonical cents with
`ROUND_HALF_UP`, and passes only exact `Decimal` amounts into the accounting
kernel. The public exporter converts final values to JSON numbers only at the
release boundary.

The authority path is:

```text
causal/economic state
    -> business transaction amounts
    -> balanced journal entries
    -> Ledger + OperationalBook + EquityBook
    -> IS / BS / direct-and-indirect CF / WASO / basic EPS
    -> canonical statement payload
    -> FilingBook
    -> public and hidden release artifacts
```

Period 0 is a real opening journal with matching receivable, payable, inventory,
PP&E, and debt detail. Opening common stock uses integer issued shares at $0.01
par; remaining opening equity is assigned deterministically to retained earnings
and APIC. If a generated liability target is not solvent, only that opening
liability target is reduced by the minimum cent-exact amount. World V2 never
creates a miscellaneous equity balance, manufactures equity from an
asset-minus-liability residual after transactions, floors cash, or inserts a
balancing entry.
Operating shortfalls become explicit debt tranches, and optional debt repayment
is a normal financing transaction.

Each quarter maps business activity to explicit sales, collections, inventory
purchases and FIFO consumption, payable payments, SG&A, capex, straight-line
depreciation, debt borrowing and repayment, interest accrual and payment, tax,
and dividends. Fraudulent reported revenue has its own issued receivable and
journal revenue; it is not collected and remains exact hidden evidence. Quarter
close occurs after all period entries. Canonical whole-second UTC instants keep
transactions, posting, close, and publication ordered.

The kernel implementation is vendored byte-for-byte from
`financial-system-core` commit
`b533ef0ddf89b6de0041b2f64dc59514f40da46f` under
`app/_vendor/fwf_kernel/`. Public CI cannot depend on that private repository
at build or runtime, so the accepted M4.1 through M4.2B source is pinned for
conformance in this repository rather than fetched, submoduled, or installed as
a new package. `SOURCE.json` records the source repository, commit, MIT license, paths,
and SHA-256 digests. The snapshot is not a local fork and its Python files must
not be edited; a focused integrity test enforces every digest. Market Fuzzer's
accepted M3 semantic RNG remains authoritative and is not replaced by a vendored
RNG.

Public financials and filing rows contain kernel-derived statement values,
filing IDs, immutable version IDs, and canonical payload SHA-256 commitments.
They do not expose journals, subledger details, fraud truth, latent state, or
RNG addresses. `hidden/accounting.json` retains the exact Decimal journal,
subledgers, equity, WASO/EPS, cash roll, derived statements, filing versions,
payloads, and reconciliation evidence as decimal strings.

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
