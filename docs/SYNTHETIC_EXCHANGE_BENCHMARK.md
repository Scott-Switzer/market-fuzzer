# Synthetic Exchange Benchmark (M10.5 vertical slice)

A procedurally generated synthetic financial world + exchange + benchmark for
training and evaluating financial AI agents. This document describes the first
vertical slice that joins the pieces already in this repository into one
reproducible synthetic trading session with sealed evaluation worlds.

## What the slice joins

```
Synthetic company universe  (app/market  — M10 factor / GJR-GARCH-t daily engine)
        +
V2 price-time-priority exchange  (app/exchange/v2_matching — LOB, submit/cancel/replace, partial fills)
        +
Deterministic background agents  (app/benchmark/agents — market maker, fundamental, momentum, noise)
        +
External agent protocol  (app/strategy_protocol — StrategyObservationV2 / StrategyActionV2)
        ↓
One complete synthetic trading session  (app/benchmark/session)
        ↓
Deterministic score  (app/benchmark/tasks)
        ↓
Sealed multi-world evaluation + replay package  (app/benchmark/runner)
```

None of the price paths, securities, order books, or events in a run existed
before the run generated them. The M10 engine supplies a *return path* (a
cross-sectional factor structure whose innovations are GJR-GARCH-t); each
security's fundamental anchor is that path scaled to its own price level. The
intraday book then emerges from agent interaction — it is not a perturbation of
real data.

## Running a benchmark

```bash
fwf benchmark run --task execution --agent http://localhost:9000 --worlds 32
```

`smw` is an alias for the same entry point (`app.cli:entrypoint`).

Flags: `--task execution|market_making|portfolio`, `--worlds`, `--securities`,
`--days`, `--steps-per-day`, `--seed`, `--policy` (built-in baseline when no
`--agent` is given), `--output` (write the replay package JSON).

HTTP agents must be reachable at a host in `FWF_BENCH_ADAPTER_ALLOWED_HOSTS`
(default `127.0.0.1,localhost`). An agent receives a `StrategyObservationV2`
document per step and must return a `StrategyActionV2` document; a protocol
failure fails closed to a deterministic hold and increments the port's error
counter.

Example headline output (32 worlds, 8 securities, 5 days):

```
FINANCIAL WORLD FACTORY BENCHMARK
Agent: builtin-twap
Task: Optimal Execution
Evaluation worlds: 32 sealed synthetic worlds
Securities encountered: 8
Exchange events: 1,818,840

Completion                 98.9%
Implementation shortfall    44.5 bps
Tail shortfall            572.4 bps
Max inventory exposure    69429
Constraint violations         0

Public worlds score        78.1
Hidden worlds score        80.4
Generalization gap         +2.3
```

## Determinism and replay

- Every exchange command carries a globally monotonic `(time_ns, venue_sequence)`
  pair from a single session clock, so the V2 event ledger is totally ordered
  regardless of which agent acted first.
- The session ledger digest, the M10 `market_logical_sha256`, and a digest over
  every agent action are recorded per world. Re-running the same world with the
  same agent reproduces the ledger byte-for-byte.
- `run_benchmark` is reproducible: the replay package digest is identical across
  runs for a fixed seed and agent.

## Anti-memorization holdout

Unseen seeds alone are not enough — an agent can overfit the generator family.
The runner partitions worlds into **public** and **hidden** families:

- public: `PUBLIC_PROFILE` (baseline volatility, depth, zero fees, agent mix A);
- hidden: `HIDDEN_PROFILE` (mechanism holdout — different volatility, thinner
  depth, taker fees, crowding), sharing the same public interface.

The **generalization gap** is `hidden_score - public_score`. A large negative gap
means the agent has adapted to the public generator rather than to the task.

## Tasks and scoring

Scores are bounded `[0, 100]` and derived only from the recorded session result.

- **Optimal Execution** — buy a target quantity before the session ends.
  `score = 100 · (0.6 · completion + 0.4 · shortfall_score)` where
  `shortfall_score = clamp(1 - max(0, shortfall_bps)/50)`. Metrics: completion,
  implementation shortfall (bps), market impact, tail shortfall, max inventory.
- **Market Making** — post two-sided quotes. Metrics: P&L (bps), quote uptime,
  maker share, max inventory, drawdown.
- **Portfolio / Trading Agent** — manage a multi-security book. Metrics: total
  return, max drawdown, turnover.

Every world's score is reduced by 25 points per constraint violation (rejections
recorded by the V2 risk engine, invalid lifecycle references, malformed actions).

## Layout

```
app/benchmark/
  model.py        shared contracts (TaskSpec, SessionResult, TaskOutcome)
  hashing.py      canonical JSON + SHA-256 helpers
  universe.py     synthetic securities + M10 price paths + holdout profiles
  agents.py       background-agent archetypes emitting V2 order intents
  port.py         in-process + HTTP decision ports, built-in baselines
  session.py      the multi-security LOB session (the join point)
  tasks.py        task specs and deterministic scorers
  runner.py       multi-world runner, partitions, report, replay package
```

## Limitations

- One session clock tick per exchange command; there is no wall-clock latency
  simulation inside the slice yet (the V1 exchange retains `latency_profile`,
  but the benchmark drives the V2 matching engine).
- The background-agent population is a small, interpretable archetype set, not a
  calibrated participant mix.
- Mechanism holdout currently varies volatility, depth, fees, and agent mix; it
  does not yet vary the *process family* (e.g. jump diffusion, stochastic
  liquidity).
- Market making and portfolio scorers are first-pass and not yet stress-tested
  against adversarial agents.
