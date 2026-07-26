# ADR 0007: Strategy identity vs. content, and the immutable approved snapshot

- Status: Accepted (Phase 1.1)
- Date: 2026-07-25
- Supersedes parts of ADR 0004 (canonical StrategySpec)

## Context

The Phase 1 `StrategySpec` conflated *identity* and *content*:

- `strategy_id` defaulted to the canonical content hash, so editing a strategy
  produced a brand-new "strategy" instead of a new version of the same one.
- `canonical_hash` was a stored, mutable field that could drift from the content
  it was supposed to summarize.
- The "approved" object (`StrategyVersion`) was not immutable, and approval used
  `model_copy(update=...)`, which Pydantic explicitly does **not** validate.

These are foundation-level defects: every later stage (runs, failures, evidence,
exchange replay) keys off the strategy hash and identity.

## Decision

1. **Identity is a standalone UUID.** `strategy_id` is a UUID4 minted at
   construction, never derived from content. A logical strategy keeps its
   `strategy_id` across edits; each edit increments an integer `version`.
   `canonical_hash` is a pure function of semantic content.

   - DB: `UNIQUE(strategy_id, version)` + non-unique index on `canonical_hash`.
   - Two independently-created strategies may share a content hash while keeping
     distinct identities.

2. **`canonical_hash` is a computed property, not stored state.** `StrategySpec`
   exposes `canonical_json()`, `compute_hash()`, and a `canonical_hash` property
   that recomputes. There is no mutable hash field to drift, and no
   `object.__setattr__` gymnastics to keep it in sync. Persistence stores the
   hash *next to* the canonical JSON; every run boundary re-asserts
   `stored_hash == spec.compute_hash()`.

3. **The approved snapshot is immutable by construction.**
   `ApprovedStrategyVersion` is `frozen=True, extra="forbid"` and stores the
   **canonical JSON string** plus its hash as the source of truth. Because the
   payload is a string, nested mutation is structurally impossible. The
   executable spec is reconstructed only by validating that stored JSON, and its
   hash is re-verified on every reconstruction (`to_spec()` raises on tamper).

4. **Approval never uses `model_copy(update=...)`.** `DraftStrategy.approve()`
   builds the approved object via `ApprovedStrategyVersion.model_validate({...})`
   with fully-validated inputs.

5. **Contract-level numbers are `Decimal`.** Weights, exposures, cost bps, and
   hashing thresholds use a canonicalized `Decimal` so `0.60` and `0.6` hash
   identically and NaN/Inf are rejected. Floats are used only at the NumPy
   execution boundary.

## Consequences

- Editing a strategy is now a *version bump*, not a new identity.
- Hash drift is impossible: there is nothing to drift.
- An approved version that round-trips through the database is byte-for-byte
  identical and re-verifies its hash.
- Downstream code must call `spec.compute_hash()` / `approved.verify()` at
  boundaries rather than trusting a stored field.
