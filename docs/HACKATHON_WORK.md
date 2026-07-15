# OpenAI Build Week work boundary

Repository baseline established on 2026-07-14 in commit `577cc54` on branch `codex/build-week-synthetic-market-world`.

## Newly written during the hackathon

- Strict versioned world specification, canonical serialization, and SHA-256 hashing
- Exact integer-tick price-time-priority order book with limit, market, cancel, partial-fill, fee, accounting, latency, and halt behavior
- Three synthetic issuers, macro factor, scheduled information events, and seven heterogeneous agent roles
- TWAP and participation-of-volume execution behavior
- Four common-seed counterfactual scenarios and 24-run quick experiment battery
- JSON/YAML/Parquet artifact bundle, manifest hashes, component realism report, and evidence-gated failure surface
- Offline compiler, optional GPT-5.6 structured compiler, CLI, FastAPI service, and browser laboratory
- Tests, reproducibility checks, CI, Docker setup, documentation, and Devpost draft
- Aggregate-only `CalibrationPackV1`, chronological holdouts, bootstrap parameter ensemble, and identifiability evidence
- Queue-reactive order-flow provider with six event types and sparse-state backoff
- 96-world paired intervention campaign and exact participation-cost claim gate
- Five-vector fit-for-use validation and separate confidentiality/derivation release validation
- Hashed `SyntheticMarketPackage` with latent regimes and intervention labels

## Adapted open-source code

None. No simulator code was copied, adapted, or vendored.

## Architectural inspiration

ABIDES, ABIDES-JPMC, JAX-LOB, DeepMarket/TRADES, and MarS were inspected at the exact revisions recorded in `THIRD_PARTY_NOTICES.md`. Their concepts informed boundaries and evaluation; the implementation is original.

## Pre-existing personal work

None was imported. Private financial data, FENRIX collaboration assets, MIT research assets, Bloomberg data, and unpublished work are outside this repository.

## Deferred work

Institutional order-flow calibration, learned responsive flow, optional ABIDES backend, accelerator/vectorized books, multiple venues, and production controls remain roadmap items.

## Market Fuzzer product rebuild

The primary product is now the compact deterministic Market Fuzzer harness. Earlier exact-exchange, synthetic-world, calibration, and validation work remains secondary research infrastructure and is documented separately from the product acceptance path.

- Deterministic fragile/corrected POV execution state machine with delayed observations and pending-order accounting
- Participation-targeted bounded search, seed reproduction, minimization trace, and verified passing neighbor
- Exact same-scenario corrected comparison with scenario/seed/parent-order contract
- Schema-validated YAML/JSON fixtures, CLI replay, and real API regression-suite execution
- Browser workflow rebuilt around Strategy → Safety Properties → Baseline → Break My Strategy → Replay → Retest → Regression
# Market Fuzzer rebuild

The primary browser route now leads with the developer workflow rather than calibration objects: strategy, safety requirements, baseline, adverse search, counterexample, replay, and regression export.
