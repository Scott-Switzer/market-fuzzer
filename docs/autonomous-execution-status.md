# Autonomous execution status

## Product objective

Build a sealed, procedurally generated market-evaluation platform that measures strategy robustness across declared mechanisms and hidden world families. It does not claim unbiasedness, impossible memorization, universal realism, or live profitability.

## Current milestone

M7 — historical calibration boundary.

- Current branch: `codex/m7-calibration-boundary`
- Current PR: not yet opened
- Clean-main baseline SHA: `3f1bb0d`
- Latest substantive Greptile-reviewed SHA: `0fda71eb2c8832b61e30a5a1a3f690be677bd7c1`
- Latest self-reviewed SHA: `3f1bb0d` (M6 merge; M7 work is uncommitted)

## Completed evidence

- M0A merged as PR #14 at `591780357335fc0e603c8c3fff5340700687dabf`.
- M0B merged as PR #15 at `e042536397b4655ba11171b6a0e9da0ff581c7c7`; review controls and secret-safe ignore rules are now on `main`.
- M1 merged as PR #16 at `1f0162a37ca9fa46c7d6e3711c1e9b79db09faf2`; the sealed-evaluation contract and migration map now govern implementation.
- M2 merged as PR #17 at `32302ac4f891c29f4a67d2a155b7459efa22125b`; immutable V2 command, event, manifest, scheduler, and ledger primitives are now on `main`.
- M3 merged as PR #18 at `b4bc0091fbae11d0c4e666eb0c3fa6cbd82737d0`; V2 cash-like price-time matching, reservations, settlement, fees, and lifecycle tests are now on `main`.
- M4 merged as PR #19 at `6b8235ca2fa11253a803da14b95f1583784cbb4c`; three interpretable generator families now provide versioned assumptions, parameter manifests, diagnostics, claims, and limitations.
- PR #19 received latest-head self-review with zero unresolved GitHub threads. Two complete GitHub verification runs passed (6m49s and 7m19s); two Docker smoke jobs passed (35s and 39s).
- M5 merged as PR #20 at `cf2973dcbfa6d511742ab3e5a104c72e2d64ffbd`; sealed campaign commitments, frozen artifacts, hidden family and parameter commitments, neutral observations, replay, and post-finalization reveal verification are now on `main`.
- PR #20 received latest-head self-review with zero unresolved GitHub threads. Two complete GitHub verification runs passed (5m23s and 6m59s); two Docker smoke jobs passed (35s and 38s).
- M3B merged as PR #21 at `6b54dc309208fedd6ae339caaebf4976dbba2758`; V2 now enforces session close expiry, instrument halts, account risk limits, kill switches, native replace priority semantics, and typed conservation failures.
- PR #21 received latest-head self-review with zero unresolved GitHub threads. Two complete GitHub verification runs passed (6m19s and 6m32s); two Docker smoke jobs passed (39s each).
- Decision evidence now degrades independently for unavailable, failed, and partial benchmark responses; browser coverage asserts those paths and the complete response.
- M0A local `make verify`, browser E2E, and Docker smoke passed. GitHub test and Docker checks passed on `f1af98feaccaf48d9cc45524e195562216fef46f`.
- Clean `main` was checked out and its verification and Docker smoke were rerun after the merge.
- M6 implementation has local evidence: canonical development, sealed-primary, and adaptive-diagnostic envelopes; Arena and Stress Lab legacy outputs label themselves as development fixtures; Market Fuzzer labels strategy-aware failure searches as adaptive diagnostics; governed report exports retain the evaluation scope and evidence digest.
- M6 local validation passed: focused evidence/Arena/Product/registry tests, full `make verify`, clean browser E2E, static analysis, and `DOCKER_DEFAULT_PLATFORM=linux/amd64 make docker-smoke`.
- M6 merged as PR #22 at `3f1bb0d`; two latest-head GitHub verification runs passed (7m17s and 7m36s), two Docker smokes passed (36s and 41s), and no inline review threads remained.

## Current work and next executable action

- Add typed source-resolution manifests, explicit calibration claims, and non-overlapping holdout evidence without reading raw Portfolio Engine data.
- Generated-world similarity remains a transient diagnostic against copying, not a novelty guarantee or hidden-evaluation source.
- Next executable action: self-review the M7 manifest and similarity implementation, run full verification, and open a focused PR.

## Unresolved findings and blockers

- Greptile trial credits are exhausted. Its status check passed on PR #14, but it emitted a credit-limit notice instead of a substantive latest-head review. Per operator direction, self-review supplements unavailable Greptile review until service capacity returns.
- The local Docker daemon's native arm64 `python:3.12-slim` image reports an exec-format error; the Docker smoke passes through `linux/amd64` emulation, while GitHub Docker checks pass natively. This is a local environment limitation, not a product claim.

## Roadmap status

| Milestone | Status |
| --- | --- |
| M0A — decision-evidence repair | merged and clean-main verified |
| M0B — execution controls | merged and clean-main verified |
| M1 — product contract and threat model | merged and clean-main verified |
| M2 — deterministic event kernel | merged and clean-main verified |
| M3 — exchange correctness | merged and clean-main verified |
| M3B — V2 lifecycle and risk controls | merged and clean-main verified |
| M4 — generator ensemble | merged and clean-main verified |
| M5 — sealed evaluation protocol | merged and clean-main verified |
| M6 — shared evaluation evidence integration | merged and clean-main verified |
| M7 — historical calibration boundary | in progress |
| M8–M10 | pending |

## Claims currently permitted

- Deterministic synthetic demo evidence inside declared worlds.
- Bounded strategy-stress workflows, role-scoped release views, and documented simulator mechanics where covered by tests.
- M0A decision evidence is a deterministic demo fixture, not commercial validation.
- M4 generator paths are interpretable synthetic event streams with explicit assumptions, not copied historical data or order-level calibration evidence.

## Claims currently prohibited

- Unbiased or impossible-to-memorize evaluation.
- Live profitability, universal market realism, best execution, production readiness, or all-asset-class coverage.
- Order-level calibration, queue-position realism, cancellation behavior, or fill probability inferred solely from OHLCV data.
- A customer-supplied strategy has not yet been executed in an isolated no-network runner; M5 only establishes the sealed campaign and observation boundary. M8 must enforce production runtime isolation.
- The existing Arena benchmark matrix uses declared fixed seeds and variants; it is development-fixture evidence, not sealed primary evaluation.
