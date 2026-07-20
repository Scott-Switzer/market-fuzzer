# Isolated strategy protocol V2

Protocol V2 is for the sealed V2 exchange runner. A V2 observation contains the
same current-time market fields as V1 plus `open_orders`, which lists only the
strategy's own resting order IDs, side, remaining quantity, and limit price. It
does not reveal queue position, counterparty identity, hidden-world provenance,
future events, or another account's order state.

V2 responses use one of four explicit actions:

- `submit` carries side, order type, and quantity; limit submissions also carry
  a limit price. Market submissions receive the runner's declared protective
  collar and are sent IOC.
- `cancel` carries exactly one previously observed strategy order ID.
- `replace` carries a previously observed strategy order ID, replacement
  quantity, and replacement limit price.
- `hold` carries no command fields.

The isolated container validates the observation version, preserves the same
version in successful or fail-closed responses, and journals the exact response
before the runner admits it. The runner validates artifact, request, response,
and idempotency digests, and rejects lifecycle references that were never
exposed in that strategy's own observations. V1 actions are rejected by the V2 runner rather than
silently reinterpreted. The exchange assigns deterministic command IDs and
emits acknowledged, completed, or rejected lifecycle events.

V1 remains for legacy development adapters. It cannot express cancel or replace
and is not an eligible action protocol for sealed V2 execution.
