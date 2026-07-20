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

## Evidence migration status

`app/evaluation/evidence_v1.py` is the first shared workflow boundary. It emits a
canonical envelope with one of three mutually exclusive scopes:

- `development_fixture` for declared deterministic public fixtures;
- `sealed_primary` only for a frozen-artifact M5 campaign result; and
- `adaptive_diagnostic` for strategy-aware failure search.

The existing Arena benchmark matrix is therefore explicitly a development
fixture, and the existing Market Fuzzer search is explicitly an adaptive
diagnostic. Neither is permitted to claim sealed-primary ranking until the V2
kernel adapter and hidden campaign execution pass parity and leakage tests.

## Current gaps to close

The current product has deterministic replay, price-time-priority evidence, protected matrices, role-scoped release, bounded calibration, V2 exchange primitives, post-freeze secret campaign generation, family holdouts, an initial isolated customer-code runtime, and a multi-family sealed evaluator. M9 also provides sealed paired metric evidence and policy-bound vector reporting.

The remaining migration boundary is material: the V2 kernel has not yet become the execution adapter that turns isolated customer strategy responses into sealed-campaign metrics. Until that adapter passes parity and leakage tests, existing Arena matrices remain development fixtures and sealed callback metrics remain evaluator integration evidence rather than customer execution proof.
