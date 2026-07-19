# Sealed evaluation threat model

## Assets

- Hidden campaign seed material, world identifiers, parameter ranges, family assignments, and generator digests before release.
- Frozen strategy artifact digest, execution transcript, immutable event ledger, result digest, and commitment/reveal records.
- Calibration-source provenance and rights metadata; licensed or customer raw data never enters public fixtures or reports.
- Correct primary score, diagnostic result, and claim boundary.

## Adversaries and required controls

| Threat | Example | Required control | Evidence |
| --- | --- | --- | --- |
| Public-world memorization | Hard-coded fixture path | Limited labeled development worlds; fresh post-freeze worlds; similarity checks | Cheating strategy regression |
| Seed or ID leakage | Seed in observation/event ID | Separate internal manifest; stripped observations; opaque IDs | Payload-leak tests |
| Temporal leakage | Future event timestamp observable | Observation-time boundary and monotonic scheduler | Future-information attack test |
| Generator overfit | Strategy identifies one public family | Hidden parameters and generator-family holdouts | Same-family versus holdout evidence |
| Score gaming | Strategy changes campaign selection | Precommitted policy and independent primary selection | Campaign commitment verification |
| Runtime escape | Customer code opens network or reads host state | Digest-pinned isolated runner; no default egress; read-only bounded environment | Isolation integration test |
| Ledger mutation/retry | Duplicate command changes fills | Idempotency keys and durable response-before-admission | Retry/replay property test |
| Calibration overclaim | OHLCV represented as queue data | Resolution-specific manifests and claim validation | Data-quality report |
| UI concealment | Optional evidence failure blanks workflow | Explicit loading/unavailable/error states | Browser negative-path test |

## Trust boundaries

The evaluator, exchange, and secret campaign material are trusted services. The strategy artifact, browser, public development worlds, and uploaded calibration metadata are untrusted inputs. The application process must not import customer strategy code in production mode.

## Failure policy

Security, determinism, manifest-integrity, observation-boundary, or resource-limit failure invalidates the affected evaluation and fails closed. A diagnostic failure is never silently converted into a primary score. Missing evidence produces `insufficient_evidence`, not a positive robustness claim.

## Residual risks

Generator assumptions can still be learned, calibration can be incomplete, and finite campaigns have sampling error. The product reports these limitations; it does not conceal them behind a single realism score.
