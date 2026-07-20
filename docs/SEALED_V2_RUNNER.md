# Sealed V2 world runner

`SealedV2WorldRunnerV1` is the evaluator-owned bridge from a generated hidden
world to the V2 cash-like exchange. It is the primary-evaluation path for this
adapter; the older `metric_evaluator` callback remains a compatibility seam and
cannot be combined with a V2 world runner in the same campaign.

For each generated event, the runner admits declared external liquidity, builds
a current-time `StrategyObservationV1` from displayed quotes and current market
data, obtains one durable isolated-runtime response, validates its artifact,
request, response, and idempotency digests, then submits the action through
`MatchingExchangeV2`. The runner uses explicit collars for protocol-level
market actions because the cash-like V2 engine requires protected buy prices.
Every finalized hidden world records only an opaque receipt, observation digest,
V2 ledger digest, strategy-response-journal digest, and finite metric cells; seeds, family names, regimes,
parameters, generator versions, and raw ledger data are not strategy-facing.

To bind a container strategy, freeze
`ContainerStrategyArtifactV1.canonical_bytes`; its SHA-256 is exactly the
runtime artifact digest recorded by the isolated strategy session. The sealed
campaign service uses one streaming no-egress JSONL container per hidden world
and closes it after each world, including evaluator failures. The session must
retain its durable response journal. A mismatched or malformed response is
fail-closed and no order is admitted.

## Current scope and limits

This implements a single-venue, cash-like continuous double auction with
generated displayed liquidity. Protocol V2 supports `hold`, submit limit or
collared-market orders, cancel, and replace; observations expose only the
strategy's own open orders. The runner reports mark-to-market P&L, fills,
trades, submit/cancel/replace counts, rejections, and inventory. It does not
yet connect the V2 runner to the Arena HTTP lifecycle or establish live-market
execution fidelity.
