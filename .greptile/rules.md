# Synthetic-market review rules

Review behavior, security, timing, and claims—not merely syntax.

## Determinism and market mechanics

- Preserve deterministic replay for identical declared inputs. Flag unordered iteration, implicit clocks, random draws without declared seed material, mutable historical state, and non-idempotent commands.
- Verify price-time priority, queue ordering, order-accounting conservation, reservations, balances, positions, fees, and settlement on every matching-engine change.
- Treat fill probability, queue position, cancellations, and latency as explicit model assumptions. Flag claims or tests that infer order-level realism from OHLCV or other insufficiently granular data.
- Require negative-path coverage for rejected orders, cancels, replacements, races, risk limits, halts, session boundaries, partial responses, and unavailable dependencies.

## Sealed evaluation boundary

- Never expose hidden seeds, world IDs, generator parameters, future timestamps, strategy labels, or hidden manifests to a strategy or public client before release.
- Flag temporal leakage, deterministic identifiers that reveal hidden state, fixture memorization paths, historical-data leakage into hidden evaluation, and scoring changes without benchmark evidence.
- Primary evaluation worlds and scores must be selected independently of the submitted strategy. Adaptive fuzzing may inspect a strategy only as a separately labeled diagnostic.

## Runtime and API safety

- Flag insecure strategy execution, unrestricted network egress, unbounded inputs, unbounded resource use, swallowed failures, fail-open behavior, and UI states that conceal backend errors.
- Check that messages, commands, and persisted responses are bounded, validated, and idempotent where replay or retries are possible.
- Do not permit secrets, credentials, customer data, licensed raw data, or environment values in source, logs, artifacts, fixtures, tests, or documentation.

## Product claims and tests

- Flag unsupported claims of unbiasedness, impossible memorization, live profitability, universal realism, best execution, production readiness, or unsupported asset-class coverage.
- Require tests to be deterministic and meaningful. Do not accept weakened assertions, hidden warnings, fixture-only evidence, or skipped failing paths as a substitute for a repair.
