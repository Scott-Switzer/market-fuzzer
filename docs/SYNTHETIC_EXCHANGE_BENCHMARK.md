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
`--agent` is given), `--output` (write the replay package JSON),
`--allow-invalid`.

HTTP agents must be reachable at a host in `FWF_BENCH_ADAPTER_ALLOWED_HOSTS`
(default `127.0.0.1,localhost`). An agent receives a `StrategyObservationV2`
document per step and must return a `StrategyActionV2` document.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | A valid benchmark completed. |
| `1` | Internal benchmark failure. |
| `2` | The external agent was unavailable or protocol-invalid. |

`--allow-invalid` preserves exit `0` for connectivity and fail-closed
demonstrations. Such a run is still labelled `scoreable = false`, prints
`Official benchmark score: WITHHELD`, and cannot produce an official score.

## Evaluation validity

A transport or protocol failure of the agent is **not** a decision. Both cases
still return a deterministic hold so the exchange stays fail-closed and
replayable, but the failure is classified and the world is removed from the
official evaluation:

| Condition | Classification |
|---|---|
| Connection refused, timeout, 5xx | `INVALID_AGENT_UNAVAILABLE` |
| 4xx, malformed JSON, oversized response, wrong protocol version, invalid action schema | `INVALID_AGENT_PROTOCOL` |
| Explicit `{"action_type": "hold", ...}` from a reachable agent | valid and scoreable |

Invalid worlds are excluded from the public/hidden means. If **any** required
evaluation world is invalid, the entire run is non-scoreable.

## Anti-memorization holdout (M10.5 scope)

Unseen seeds alone are not enough — an agent can overfit the generator family.
Worlds are partitioned into **public** (`PUBLIC_PROFILE`, baseline volatility,
depth, and agent mix) and **hidden** (`HIDDEN_PROFILE`) families that share one
public interface but differ in:

- unseen seeds;
- parameter / distribution shift (volatility scale);
- liquidity shift (displayed depth);
- fee shift (taker fees);
- agent-ecology shift (market-maker/fundamental/momentum/noise populations and crowding).

M10.5 is therefore a **distribution holdout** (`hidden-distribution-holdout`),
sometimes described as a parameter-ecology holdout. Both partitions still use
the same GJR-GARCH-t process family. This is explicitly **not** a
mechanism-family / process-family holdout: true generator-family OOD evaluation
arrives in **M10.6**.

The reported **distribution generalization gap** is
`hidden_score - public_score`. A large negative gap means the agent adapted to
the public generator rather than to the task.

## Sealed-evaluation integrity guarantees

- **Model-facing identifiers are opaque.** The agent session ID is an
  instrument-independent digest over evaluator-private seed material
  (`sx-<32 hex>`). Observations never expose the world index, the
  public/hidden partition, the holdout label, the world seed, or generator
  parameters. A canary test proves evaluator-only metadata cannot reach a
  serialized observation.
- **Order ownership is explicit.** Maker/taker attribution reads a session-owned
  order-ownership map; it never parses an order-ID string.
- **Parent orders are enforced.** Execution completion and shortfall use the net
  quantity delivered on the requested side, so buying and then selling back nets
  to zero completion. The built-in TWAP sizes slices after subtracting
  outstanding same-side quantity and cancels live orders once the target is
  delivered, so `net delivered <= target` holds for that baseline.
- **Only tradable instruments exist for the agent.** Starting inventory is
  granted solely for `task.agent_instruments`, so a price move in an
  inaccessible security cannot move the agent's P&L.
- **Peak risk is historical.** Peak inventory is tracked through the session, so
  building and then flattening a position cannot hide the exposure.
- **Currency units are consistent.** Executed notional is recorded in account
  currency (cents) on every fill, so turnover is correct for any tick size.
- **Drawdown starts before the first decision.** The equity curve begins at the
  pre-decision value, so a first-step loss is inside the drawdown.

## Determinism and replay

- Every exchange command carries a globally monotonic `(time_ns, venue_sequence)`
  pair from a single session clock, so the V2 event ledger is totally ordered
  regardless of which agent acted first.
- Background agents draw from instrument-scoped semantic streams
  (`AGENT:<agent>:<instrument>`), so noise order flow is not perfectly
  correlated across securities, while remaining fully deterministic.
- The session ledger digest, the M10 `market_logical_sha256`, and a digest over
  every agent action are recorded per world. Re-running the same world with the
  same agent reproduces the ledger and the whole replay-package digest
  byte-for-byte.

## Tasks and scoring

Scores are bounded `[0, 100]` and derived only from the recorded session result.

- **Optimal Execution** — buy a target quantity before the session ends.
  `completion = clamp(net_delivered / target, 0, 1)` where `net_delivered` is the
  parent-side quantity net of opposite-side fills. Shortfall uses the net cost
  basis per net share delivered.
  `score = 100 · (0.6 · completion + 0.4 · shortfall_score)` with
  `shortfall_score = clamp(1 - max(0, shortfall_bps)/50)`.
- **Market Making** — post two-sided quotes. Metrics: P&L (bps), quote uptime,
  maker share, peak inventory, drawdown.
- **Portfolio / Trading Agent** — manage a multi-security book. Metrics: total
  return, max drawdown, turnover.

Every world's score is reduced by 25 points per constraint violation.

## Example output

`--worlds 32 --securities 8 --days 5 --steps-per-day 30` (≈77 s):

```
FINANCIAL WORLD FACTORY BENCHMARK
Agent: builtin-twap
Task: Optimal Execution
Evaluation worlds: 32 sealed synthetic worlds
Valid worlds: 32
Securities encountered: 8
Exchange events: 1,814,812

Completion                 97.5%
Implementation shortfall    55.8 bps
Tail shortfall            589.1 bps
Peak inventory exposure   50000
Constraint violations         0

Public worlds score        79.7
Hidden worlds score        78.2
Distribution generalization gap   -1.5

Weakest environment:
hidden / hidden-distribution-holdout (world bench-0001-hidden)

Replay package: 32 worlds, digest f260df5330dcba6451c62cdf8a933bf9

Validity: VALID
```

## Layout

```
app/benchmark/
  model.py        shared contracts (TaskSpec, SessionResult, TaskOutcome, validity)
  hashing.py      canonical JSON + SHA-256 helpers
  universe.py     synthetic securities + M10 price paths + holdout profiles
  agents.py       background-agent archetypes emitting V2 order intents
  port.py         in-process + HTTP decision ports, failure classification, built-ins
  session.py      the multi-security LOB session (the join point)
  tasks.py        task specs and deterministic scorers
  runner.py       multi-world runner, partitions, validity, report, replay package
```

## Limitations

- **Distribution holdout only.** Hidden worlds vary parameters, liquidity, fees,
  and agent ecology, but not the process family. True mechanism-family holdout
  is M10.6.
- One session clock tick per exchange command; there is no wall-clock latency
  simulation inside the slice.
- Marks are last-traded-price; an aggressively filled order can therefore show a
  small paper gain from multi-level fills.
- Market making and portfolio scorers are first-pass and not exhaustively
  adversarial; the adversarial suite covers round trips, overfill, invalid
  lifecycle spam, maker credit, and agent unavailability.
- The background-agent population is a small, interpretable archetype set, not a
  calibrated participant mix.
