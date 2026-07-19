# Sealed evaluation migration map

## Reusable current capabilities

| Current surface | Reuse path | Migration condition |
| --- | --- | --- |
| `app/simulation.py`, `app/exchange/` | Matching and deterministic simulation reference | Retain behind a kernel adapter until V2 passes equivalent queue/fill/replay tests |
| `app/execution_arena.py` | Arena policy/evidence workflow | Consume shared evaluator manifests rather than bespoke matrices |
| `app/product.py` and `/market-fuzzer` | Adaptive failure-search UX | Consume shared ledger and label outputs diagnostic |
| `app/experiments/`, calibration modules | Aggregate calibration provenance | Preserve resolution boundaries and non-overlapping holdouts |
| `app/execution_store.py` | Lifecycle/audit persistence | Adapt manifests without exposing hidden fields |
| Existing replay/browser tests | Regression proof | Keep or strengthen during adapter migration |

## Planned adapters

1. Introduce versioned kernel types beside existing simulation types; do not delete working interfaces.
2. Adapt current world schemas to `InstrumentSpec` and venue/session/settlement rule objects.
3. Write immutable V2 commands/events and replay them alongside the legacy engine on deterministic fixtures.
4. Move Arena and Market Fuzzer read paths to shared manifests only after parity tests pass.
5. Remove legacy paths only when the replacement has equal-or-stronger tests and an economically justified compatibility decision is recorded.

## Current gaps to close

The current product has deterministic replay, price-time-priority evidence, protected matrices, role-scoped release, and bounded calibration. It does not yet provide a typed immutable V2 ledger, post-freeze secret campaign generation, family holdouts, isolated customer-code execution, or a multi-family sealed evaluator. Those gaps define M2 through M9; none are represented as completed capability.
