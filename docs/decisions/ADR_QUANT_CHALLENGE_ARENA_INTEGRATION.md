# ADR: Quant Challenge Arena integration

**Status:** Accepted for the Build Week MVP
**Date:** 2026-07-15

## Decision

The submission repository remains `/Users/scottthomasswitzer/Documents/OAI_Build_Week` and the primary product becomes **Quant Challenge Arena**. The existing Market Fuzzer workflow remains available as the `Execution Robustness Challenge` foundation and is not deleted or rewritten.

The specified FenrixQuant shell at `/Users/scottthomasswitzer/Documents/FenrixQuant` and Zion repository at `/Users/scottthomasswitzer/Documents/zion-terminal` were not present during this audit. No code was copied from those paths, and no ownership or license assumptions were made. The Arena MVP therefore uses the current FastAPI/static application shell and adds a module-level portfolio challenge engine under `app/arena.py`.

## Reuse boundary

### Existing Market Fuzzer work

- Deterministic seeds and stable hashes
- Versioned policy/configuration concepts
- Scenario manifests and evidence references
- Stress comparisons and regression fixtures
- Exact replay and no-key operation
- Existing POV state machine preserved under the current product path

### New Build Week Arena work

- Versioned challenge schema and public/hidden data boundary
- Deterministic daily synthetic regime generator
- Strict CSV position-submission contract
- Public and hidden portfolio scoring
- Integrity checks for hidden dates, temporal alignment, false-feature collapse, and delay sensitivity
- Example ranking reversal and instructor/student APIs
- Structured GPT-5.6 challenge and feedback schemas with deterministic fallbacks
- Arena browser workflow and tests

### Zion concepts

Zion is referenced only conceptually for deterministic schemas, provenance, validation, and source-aware evidence. No Zion files or data were imported.

## Why this architecture

The existing Market Fuzzer repository already has a verified deterministic testing engine and release/CI path. A module-level Arena layer gives the submission the requested education product without destabilizing the proven execution workflow or forcing an execution state machine into portfolio-position evaluation. It also keeps hidden challenge data server-side and makes the public-versus-robustness ranking reversal testable in one repository.

## Attribution

The repository baseline and earlier Market Fuzzer commits predate this Arena correction. Build Week additions are recorded in `docs/BUILD_WEEK_PROVENANCE.md` and the commit history. The absent FenrixQuant and Zion paths remain explicitly documented as unintegrated dependencies rather than being represented as reused code.
