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

The protected Market Fuzzer product milestone is commit `496fcc1` and is tagged locally as `market-fuzzer-milestone-1`. It remains available at `/market-fuzzer` as a secondary Execution Robustness Challenge. Earlier exact-exchange, synthetic-world, calibration, and validation work remains secondary research infrastructure.

- Deterministic fragile/corrected POV execution state machine with delayed observations and pending-order accounting
- Participation-targeted bounded search, seed reproduction, minimization trace, and verified passing neighbor
- Exact same-scenario corrected comparison with scenario/seed/parent-order contract
- Schema-validated YAML/JSON fixtures, CLI replay, and real API regression-suite execution
- Browser workflow rebuilt around Strategy → Safety Properties → Baseline → Break My Strategy → Replay → Retest → Regression

## Quant Challenge Arena integration

The primary product is now the exchange-backed Quant Challenge Arena for the Education track. `app/execution_arena.py`, `app/execution_store.py`, `app/execution_challenge_designer.py`, `app/execution_feedback.py`, and `/api/arena/execution/*` own that path. The earlier `app/arena.py` generated-panel/CSV assessment remains an explicitly secondary research challenge; Market Fuzzer remains the advanced lab.

New primary Arena work includes:

- Strict versioned TWAP/POV/adaptive-POV policy configuration with bounded controls and no participant code execution
- Public practice derived from stored server state and protected server-selected liquidity/latency/crowding/event worlds
- Server-generated, resumable signed demo identities, instructor code, phase authorization, and separate test-only header bypass
- SQLite users, sessions, challenges and protected-world manifests, phase history, drafts, final policies, practice runs, immutable evaluations, world results, leaderboard snapshots, qualitative design drafts, persisted feedback, and audit events; state/audit and quota count/write operations are transactional
- Fill-derived participation, inventory and child-order conservation, explicit order/ack/fill/cancel times, order-hygiene evidence, and simplified price-time-priority queue evidence
- Multi-world/multi-seed public-versus-robustness ranking reversal with a stored score decomposition and matrix hash
- Synchronized market/strategy replay, policy-by-world results, and public-to-hidden rank movement
- GPT-5.6 qualitative challenge-design drafts and release-safe overall/intent plus public-trace evidence analysis with strict local validation, persisted report recovery, and a complete no-key fallback
- Security, metric, metamorphic, persistence, GPT, Playwright browser, Docker, and CI verification

The referenced `/Users/scottthomasswitzer/Documents/FenrixQuant` and `/Users/scottthomasswitzer/Documents/zion-terminal` paths were not present during the original redirect audit. No files, data, or collaboration assets from those projects were imported. See `docs/BUILD_WEEK_PROVENANCE.md`.
