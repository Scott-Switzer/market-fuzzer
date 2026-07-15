# Build Week provenance

## Pre-existing or previously completed Market Fuzzer work

- The protected milestone is tagged `market-fuzzer-milestone-1` at commit `496fcc1`.
- The compact POV harness, deterministic stress search, minimization, replay, regression fixtures, and original browser workflow are preserved.
- Earlier synthetic-world and exchange modules remain secondary research infrastructure.

## New Arena extension

The Quant Challenge Arena extension adds:

- `app/arena.py`: challenge schema, deterministic regimes, CSV validation, scoring, integrity checks, examples, and evidence-grounded fallback feedback.
- Arena API routes in `app/api/app.py`.
- `app/static/arena.html` and `app/static/arena.js`: instructor/student workflow.
- `tests/test_arena.py`: deterministic generation, hidden-data isolation, validation, scoring, ranking reversal, and feedback-boundary tests.
- `docs/decisions/ADR_QUANT_CHALLENGE_ARENA_INTEGRATION.md` and this file.

## Not imported

The requested FenrixQuant and Zion repositories were not found at their specified local paths during the integration audit. No private datasets, authentication code, classroom artifacts, or unresolved third-party code were copied into this repository.

## Claim boundary

The Arena MVP is an education-oriented deterministic assessment prototype. It does not claim live trading, institutional calibration, student misconduct detection, or definitive academic-integrity findings. Integrity outputs are evidence labels that support instructor review.
