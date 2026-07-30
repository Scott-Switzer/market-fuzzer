# ADR-0004: Canonical declarative StrategySpec

- **Status:** Accepted (Phase 1)
- **Date:** 2026 reset

## Context
The prior product drifted: the public surface hard-coded a flagship long/short
momentum strategy while displaying preset dropdowns it did not execute, and the
plain-English thesis never reached the executor (`SubmissionRequest` was
`extra="forbid"` with no `description`). Different stages risked different
strategy representations. The reset brief (section 5, section 16-19) requires ONE
authoritative, versioned strategy contract consumed identically by every stage,
with a canonical hash that binds all stages together.

## Decision
Introduce `app/domain/strategy_spec.py::StrategySpec` (schema
`strategy-spec/v1`) as the single canonical contract. Properties:

1. **Full declarative field set** (thesis, intended use, known limitations,
   expected failures, universe, benchmark, signals, portfolio construction,
   costs, risk, execution timing, clause ledger, assumptions, user resolutions).
2. **Deterministic canonical hash** computed over a normalized serialization
   that EXCLUDES volatile keys (`strategy_id`, `strategy_version`,
   `compiler_metadata`, `canonical_hash`, timestamps). The excluded set lives in
   one constant (`VOLATILE_KEYS`) so both sides that hash a spec exclude the SAME
   keys — a divergence there silently breaks the hash invariant (a known repo
   pitfall).
3. **Clauses are never silently dropped.** Unsupported/unresolved clauses are
   preserved in `unsupported_clauses` / `clauses` and block execution.
4. **Execution gating** via `blocking_reasons()` — fails closed on unresolved
   clauses, missing data, unsupported type, contradictory exposure, look-ahead
   same-bar execution, invalid universe, benchmark-in-universe, and arbitrary
   code requests.

## Consequences
- Every compiler, template, backtest, synthetic run, exchange replay, and
  evidence package consumes the same object and the same hash (gate 19).
- Templates become examples that compile to a `StrategySpec`, not alternate
  hidden code paths (gate 1).
- The existing DSL `Strategy` (`app/strategy_lab/dsl.py`) remains for the current
  executor during migration; Phase 2 maps it onto `StrategySpec`. Two hashing
  schemes coexist only transitionally and are reconciled by a conformance test
  before the old one is retired.

## Alternatives rejected
- Keeping the ad-hoc `CrossSectionalSpec` dataclass as the contract: it encodes
  only the flagship strategy and cannot represent families B–E.
- Using the LLM output directly as truth: prohibited (section 20) — the LLM may
  propose clauses but may not bypass schema validation or user approval.
