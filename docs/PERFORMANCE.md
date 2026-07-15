# Performance evidence

These are local development measurements, not production benchmarks, capacity claims, or an SLA. Hardware, Python version, cold filesystem state, browser cache, and concurrent processes affect them.

## Reproduce

```bash
make install
make install-browser
make performance
```

`scripts/performance_probe.py` reports JSON with environment context, workload, matrix hash, and timings for:

- application process start until `/api/health` succeeds;
- public practice, median of three deterministic runs;
- one policy across the persisted protected-world manifest and internal `SEEDS`;
- the complete built-in benchmark matrix;
- a SQLite practice write, audit write, and challenge read, median of five;
- deterministic no-key GPT fallback, median of five; and
- headless Chromium initial page load through network idle and visible hero.

The probe uses a temporary SQLite database and removes it. It removes `OPENAI_API_KEY` for the fallback measurement and does not make a live model call.

## Verified local measurement

Measured on 2026-07-15 at 21:22:55 UTC against implementation commit
`b0d7609248864ccf96f9516dca8cea0dd7061836`. The environment was macOS 26.5.1 on arm64 with
Python 3.14.5. The immutable built-in matrix hash was
`83ae410d14f0c3253ca1995e31d732268bf762dee7d65e05dcf03829207bcd57`.

| Operation | Measured wall time | Fixed workload contract |
| --- | ---: | --- |
| Application startup to healthy | 5,194.62 ms | Fresh Uvicorn process and SQLite initialization |
| Public practice | 1,169.05 ms | Aggressive POV, stored public world, exact seed 42; median of 3 |
| One-policy protected evaluation | 9,474.33 ms | Aggressive POV, 4 protected worlds × seeds 41 and 42 = 8 runs |
| Full benchmark matrix | 52,695.38 ms | 4 built-ins × canonical public/protected contract |
| SQLite persistence operation | 179.66 ms | Practice write + audit write + challenge read; median of 5 |
| GPT fallback | 0.53 ms | Released aggregate/public-trace evidence, no key; median of 5 |
| Browser initial load | 1,032.77 ms | Headless Chromium, network idle and hero visible |

Complete probe output:

```json
{
  "claim_boundary": "Local development evidence only; not a production benchmark or SLA.",
  "environment": {
    "code_commit": "b0d7609248864ccf96f9516dca8cea0dd7061836",
    "machine": "arm64",
    "platform": "macOS-26.5.1-arm64-arm-64bit-Mach-O",
    "processor": "arm",
    "python": "3.14.5"
  },
  "measured_at": "2026-07-15T21:22:55.604531+00:00",
  "measurements_ms": {
    "application_startup_to_healthy": 5194.62,
    "browser_initial_load_network_idle": 1032.77,
    "full_benchmark_matrix": 52695.38,
    "gpt_no_key_fallback_median_of_5": 0.53,
    "one_policy_hidden_matrix": 9474.33,
    "public_practice_median_of_3": 1169.05,
    "sqlite_practice_write_audit_read_median_of_5": 179.66
  },
  "workload": {
    "feedback_mode": "deterministic_fallback",
    "full_matrix_policy_count": 4,
    "hidden_seeds": [41, 42],
    "hidden_world_count": 4,
    "matrix_hash": "83ae410d14f0c3253ca1995e31d732268bf762dee7d65e05dcf03829207bcd57",
    "one_policy_hidden_runs": 8,
    "public_seed": 42
  }
}
```

## Runtime behavior

- Public practice runs only the stored public world and never calls the hidden matrix.
- Public leaderboard reads public-only results; it does not compute protected worlds on page load.
- Hidden evaluation is run once per locked challenge and persisted with a deterministic matrix hash.
- Subsequent leaderboard and release requests read immutable SQLite snapshots.
- Release atomically changes visibility, phase, timestamps, and audit state without recomputing the matrix.
- The no-key feedback path is local and deterministic.
- Playwright E2E intentionally runs the real matrix and is therefore slower than an API health smoke.

## Caching contract

Immutable evaluation identity is derived from challenge, policy, world, seed, exchange/agent/scoring versions, and result content. A change to a policy, world, seed, or relevant version changes the hash and prevents stale reuse. SQLite de-duplicates the stored challenge evaluation; raw page loads do not trigger hidden recomputation.

## Scaling boundary

The current single-process SQLite design is appropriate for a local Build Week demonstration. It does not claim multi-instance scheduling, distributed evaluation workers, GPU batching, high availability, or institutional class-scale throughput. A future worker pool could parallelize independent policy/world/seed cells only if deterministic ordering, hash construction, and audit semantics remain unchanged.
