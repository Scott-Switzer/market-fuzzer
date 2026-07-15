# ADR: Quant Challenge Arena integration

**Status:** Accepted; execution vertical slice supersedes the original CSV-first UI decision
**Date:** 2026-07-15

## Decision

The primary product is the exchange-backed **Quant Challenge Arena** at `/` and `/api/arena/execution/...`.

The repository hierarchy is:

```text
Primary        Execution Challenge Arena
Secondary      Generated-panel research/positions challenge
Advanced       Protected Market Fuzzer
Infrastructure Synthetic world, agents, and exchange
```

The originally added `app/arena.py` portfolio/CSV assessment is preserved under `/api/arena/challenges/...` as an experimental secondary challenge. It no longer defines the homepage or primary submission story. The protected Market Fuzzer remains available at `/market-fuzzer` and its tagged milestone is not rewritten.

## Execution architecture

- `app/challenges/base.py` defines the shared challenge interface.
- `app/challenges/execution/` and `app/challenges/research/` adapt their different submission contracts without conflating them.
- `app/execution_arena.py` validates policy configurations and evaluates the synthetic exchange.
- `app/execution_store.py` persists server-generated identities, lifecycle and protected-world manifest, submissions, immutable evaluation, qualitative design drafts, release, feedback, and audit state in SQLite.
- `app/execution_challenge_designer.py` and `app/execution_feedback.py` bound GPT-5.6 to structured educational intent and verified evidence interpretation.
- `app/api/app.py` owns identity, phase authorization, and public/released/instructor view projection.

## Why

Execution policy behavior provides a more coherent connection between the education category and the repository’s real exchange infrastructure. The learner can see orders, fills, latency, participation, inventory, and impact—not only submit a static positions CSV. A protected world matrix still creates the central teaching reveal: the visible winner can lose after robustness testing.

Separating challenge adapters preserves the valid secondary research exercise and avoids forcing its CSV schema into execution. Preserving Market Fuzzer maintains the developer-tool evidence and protected provenance boundary.

## Security and evidence consequences

- Public practice has no hidden-world selector.
- Signed demo sessions replace normal client role headers; the server generates and resumes identity, while instructor issuance requires a server-side code.
- Public practice is fixed to the stored world at seed `42`; protected evaluation is fixed to the persisted hidden-world manifest and internal `SEEDS`.
- Lifecycle, evaluation, qualitative design drafts, and feedback reports persist across restart. Quota count/write and lifecycle state/audit changes are transactional.
- Release atomically changes visibility, phase, and audit state rather than recomputing scores.
- GPT cannot construct numeric worlds or alter deterministic market/scoring state. Student feedback uses release-safe overall/educational-intent aggregates plus public-trace IDs; protected seed rows, internal world IDs, hashes, and replay remain instructor-only.
- Participant input remains declarative; no arbitrary remote code execution is added.

## Reuse and attribution

The existing synthetic world, matching engine, agent ecology, deterministic hashes, and Market Fuzzer concepts are reused infrastructure. External research informed interfaces and tests; no substantial code from ABIDES, HftBacktest, JAX-LOB, EvalAI, or Codabench was copied or vendored.

The specified FenrixQuant and Zion paths were absent during the original audit. No code, data, authentication, or collaboration assets were imported from them. Commit boundaries are recorded in `docs/BUILD_WEEK_PROVENANCE.md`.
