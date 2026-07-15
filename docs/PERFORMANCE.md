# Performance notes

These are local development measurements for the compact deterministic harness, not production capacity claims. Hardware, Python version, and filesystem affect them.

## Reference workload

The golden tutorial uses:

- one built-in fragile POV strategy;
- 20 deterministic simulation steps per candidate;
- the quick search grid;
- seeds `41, 42, 43`;
- one minimized counterexample and one verified passing neighbor.

The search intentionally favors explainability and deterministic evidence over throughput. The research-grade exchange modules are not part of this reference workload.

## Local reference measurement

On the Build Week macOS workspace on 2026-07-15, the current `.venv` measured:

| Command | Wall time | Scope |
|---|---:|---|
| `smw run-example` | 1.31 s | fresh quick search, minimization, corrected retest, fixture export |
| `smw test artifacts/market_fuzzer` | 0.49 s | replay of the active fixture directory |

These values are reproducibility notes for this environment, not an SLA or a claim about production throughput.

## What to measure locally

Run:

```bash
time .venv/bin/python -m app.cli run-example >/tmp/market-fuzzer-run-example.json
time .venv/bin/python -m pytest -q
```

The CLI output records the actual candidate count, seeds, minimized severity, and fixture paths. Do not copy timings from another machine into a product claim.

## Runtime guardrails

- Quick mode uses three seeds and a bounded candidate grid.
- Deep mode uses eight seeds; it is for audit evidence, not first-run onboarding.
- The judge script creates an isolated temporary artifact root so stale fixtures cannot affect the result.
- Search results are deterministic for a fixed strategy, property profile, mode, and seeds.
- The API does not execute arbitrary uploaded Python.

## Future optimization targets

If the product grows beyond the tutorial harness, profile the pure evaluator before adding concurrency. Candidate work is embarrassingly parallel, but result hashes, seed pairing, and minimization ordering must remain stable. A future vectorized backend may be added behind the existing strategy/search contracts; it is not required for this Build Week slice.
