# Quant Challenge Arena demo script — 2:55 target

Use a fresh database and the deterministic no-key path unless the live GPT-5.6 call has already been rehearsed. Keep the browser zoom and window size fixed; do not wait for package installation or a cold container build on camera.

## 0:00–0:20 — Problem

**Screen:** Arena hero and four-stage workflow.

**Narration:** “A strategy can win the market it practiced on and fail the market it never saw. Quant Challenge Arena tests whether a learner understands execution robustness—not merely whether they optimized a visible backtest.”

## 0:20–0:45 — Configure a policy

**Screen:** Select **Aggressive POV**. Change one permitted control and show the plain-English policy summary.

**Narration:** “The learner submits a bounded declarative policy, never arbitrary code. Participation, spread, urgency, latency tolerance, cancellation, completion buffer, and halt behavior are schema-validated. The same engine adapter evaluates built-ins and learner policies.”

## 0:45–1:05 — Public practice and final submission

**Screen:** Run public practice, show completion/shortfall, replay, orders and fills, then save final.

**Narration:** “The visible synthetic exchange processes every observation, order arrival, acknowledgment, partial fill, cancellation, and inventory update deterministically at the canonical public seed. Aggressive POV looks strongest here. Practice is limited transactionally, and the final policy is stored immutably under a resumable server-generated demo identity.”

## 1:05–1:30 — Protected evaluation and reversal

**Screen:** Instructor mode. Lock submissions, evaluate hidden worlds, release. Show public-to-hidden rank movement and the policy-by-world heatmap.

**Narration:** “Hidden world names and hashes never cross the student boundary before release. The instructor locks the challenge and evaluates the persisted protected-world manifest with the internal protected seeds. The public winner falls; Guarded Adaptive POV becomes the robustness winner because the measured execution tradeoffs change.”

## 1:30–1:55 — Replay why the winner changed

**Screen:** Compare Aggressive and Guarded on the same world hash and seed. Point to spread/depth, order and fill markers, inventory, participation limit, and one evidence row.

**Narration:** “This explanation is traceable. Both policies receive the same world, seed, and parent order. Aggressive behavior consumes more adverse liquidity and is more sensitive to stressed latency; Guarded behavior controls participation and inventory path. Every fill occurs after exchange arrival, and the parent-order accounting ties.”

## 1:55–2:20 — GPT-5.6 evidence analysis

**Screen:** Request feedback. Show the source label and cited evidence IDs. If no key is present, show the deterministic fallback label.

**Narration:** “GPT-5.6 explains only release-safe overall and educational-intent aggregates plus stable IDs from this learner's public trace through a strict output schema. Raw protected world rows remain instructor-only. Unknown IDs, invented values, pre-release hidden claims, investment advice, and attempts to change score or rank are rejected. The validated report is persisted; with no key, the workflow stays complete and labels the explanation as deterministic.”

## 2:20–2:40 — Reproducibility and advanced lab

**Screen:** Matrix hash, phase audit, then open `/market-fuzzer`.

**Narration:** “Challenge, policy, worlds, seeds, engine and scoring versions produce one immutable matrix hash. SQLite survives restart. The protected Market Fuzzer remains available as an advanced lab for minimizing failures and exporting regression fixtures.”

## 2:40–2:55 — Audience, impact, boundary

**Screen:** Return to Arena limitation callout.

**Narration:** “For instructors, candidates, and quant teams, Arena turns market behavior into a reproducible assessment. It is a fictional education simulator—not alpha proof, live-trading safety, real-market calibration, or investment advice.”

## Recording evidence to capture separately

- Browser console with zero errors.
- `make verify` passing.
- `make docker-smoke` passing.
- Final GitHub Actions run.
- Final repository SHA and protected `market-fuzzer-milestone-1` tag.
