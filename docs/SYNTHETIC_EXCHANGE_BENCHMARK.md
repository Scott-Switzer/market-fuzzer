# Synthetic Exchange Benchmark (M10.6 process-family holdout)

A procedurally generated synthetic financial world + exchange + benchmark for
training and evaluating financial AI agents. This document describes the vertical
slice that joins the pieces already in this repository into one reproducible
synthetic trading session with sealed evaluation worlds.

M10.5 shipped one generator family. **M10.6 adds a true mechanism-family
holdout**: three process families behind a common interface, a clean separation
between the *process family* and the *ecology*, the familiar / distribution /
mechanism evaluation partitions, and a market-process generalization gap.

## What the slice joins

```
Synthetic company universe  (app/benchmark/universe — ecology + process family)
        +
Process families  (app/market/process + app/market/engine — GJR-GARCH-t,
                   stochastic volatility, regime-jump)
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
Sealed three-partition evaluation + replay package  (app/benchmark/runner)
```

None of the price paths, securities, order books, or events in a run existed
before the run generated them. The M10 engine supplies a *return path* (a
cross-sectional factor structure whose innovations come from a process family);
each security's fundamental anchor is that path scaled to its own price level. The
intraday book then emerges from agent interaction — it is not a perturbation of
real data.

## Process families

A **process family** is the stochastic mechanism that generates the per-session
log-return innovations of a node (the market factor, a sector factor, or a single
company). Every family implements the common interface
`ProcessFamily.innovations(stream, sessions, variable)`, so the engine's factor
arithmetic is identical for all of them.

| Family | Structure | Partition |
|---|---|---|
| `gjr_factor_t_v1` | GJR-GARCH-t: `sigma2_t = omega + alpha·eps2 + gamma·[eps<0]·eps2 + beta·sigma2`, unit-variance Student-t shocks. Volatility is a function of past squared returns. | familiar |
| `stochastic_vol_factor_t_v1` | Log-normal stochastic volatility: a latent AR(1) log-variance `h_t` with its own Gaussian shock, and `eps_t = exp(h_t/2)·z_t`. Volatility has an independent shock source. | held out (mechanism) |
| `markov_regime_jump_factor_t_v1` | A hidden two-state Markov chain with regime-specific drift and volatility, plus compound-Poisson jumps whose size is regime dependent. Stressed regimes jump harder. | held out (mechanism) |

Every family is **normalized to the same unconditional per-session variance per
node role**, so the family axis changes the *dynamics* of the path (volatility
clustering, tails, drift regimes), not its level. `variance_scale` is a pure scale
that multiplies the unconditional variance by `scale²` without touching the
dynamics — this is what keeps the ecology axis orthogonal to the family axis.

## Evaluation partitions

Each sealed world is assigned to exactly one partition by the evaluator:

| Partition | Ecology | Process family |
|---|---|---|
| `familiar` | `familiar-baseline` | `gjr_factor_t_v1` |
| `distribution` | `distribution-shift` | `gjr_factor_t_v1` |
| `mechanism` | `distribution-shift` | a held-out family (alternating `stochastic_vol_factor_t_v1` / `markov_regime_jump_factor_v1`) |

The **ecology** is the tradable environment around a price process: volatility
level, displayed depth, fee schedule, and background-agent mix. M10.6 separates it
from the process family: `EcologyProfile` carries no family or partition, and a
process family carries no ecology.

Three gaps are reported, each isolating one axis:

- **Distribution generalization gap** = `mean(distribution) − mean(familiar)`:
  the ecology/parameter shift at a fixed process family (the M10.5 holdout).
- **Mechanism generalization gap** = `mean(mechanism) − mean(familiar)`: the
  joint shift (ecology *and* unseen process family) relative to what the agent saw.
- **Process-family generalization gap** = `mean(mechanism) − mean(distribution)`:
  the **isolated market-process effect**, because the mechanism and distribution
  partitions share the same shifted ecology and differ only in the process family.

A large negative process-family gap is the signature of an agent that learned the
familiar generator — its volatility clustering, tails, and drift dynamics — rather
than the execution task.

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

Invalid worlds are excluded from the partition means. If **any** required
evaluation world is invalid, the entire run is non-scoreable.

## Sealed-evaluation integrity guarantees

- **Evaluator-private process-family injection.** The family a world uses is
  chosen by the runner and recorded only in the evaluator-side run manifest and
  the replay package. Observations never expose the partition, the process
  family, the ecology label, the world index, the world seed, or generator
  parameters. The agent session ID is an instrument-independent digest over
  evaluator-private seed material (`sx-<32 hex>`) and does **not** depend on the
  process family, so two worlds that differ only by their generator family share
  an agent-facing identity. Tests assert that no family or partition token can
  reach any serialized observation.
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
- Every family draws only from keyed semantic streams, so a change confined to one
  family cannot perturb another family's stream.
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

`--worlds 32 --securities 8 --days 5 --steps-per-day 30` (≈78 s, 1.72 M exchange
events, 32/32 distinct per-world ledger digests, two runs byte-identical):

```
FINANCIAL WORLD FACTORY BENCHMARK

Agent: builtin-twap
Task: Optimal Execution
Evaluation worlds: 32 sealed synthetic worlds
Valid worlds: 32
Securities encountered: 8
Exchange events: 1,721,028

Completion                 97.4%
Implementation shortfall   125.1 bps
Tail shortfall            534.8 bps
Peak inventory exposure   50000
Constraint violations         0

Familiar worlds score       80.6  (11 valid worlds)
Distribution worlds score   67.4  (11 valid worlds)
Mechanism worlds score      82.3  (10 valid worlds)

Distribution generalization gap      -13.2
Mechanism generalization gap          +1.7
Process-family generalization gap    +14.9

Weakest environment:
distribution / gjr_factor_t_v1 / distribution-shift (world bench-0025-distribution)

Replay package: 32 worlds, digest a6c1ee65ff10281d7d9e403d0c1045d9

Validity: VALID
```

For this non-modeling TWAP baseline the isolated process-family gap is *positive*:
it does not overfit the familiar generator. The two held-out families are still
scored apart from each other (per-world means ≈73.9 for the regime-jump family and
≈90.8 for stochastic volatility), which is direct evidence that the instrument
resolves generator-dependent behaviour rather than injecting family noise.

## Layout

```
app/market/
  process.py      ProcessFamily interface + stochastic-volatility and regime-jump families
  engine.py       factor engine; GjrGarchT is the gjr_factor_t_v1 family
app/benchmark/
  model.py        shared contracts (TaskSpec, SessionResult, TaskOutcome, validity)
  hashing.py      canonical JSON + SHA-256 helpers
  universe.py     ecology profiles, process-family axis, evaluation partitions, securities
  agents.py       background-agent archetypes emitting V2 order intents
  port.py         in-process + HTTP decision ports, failure classification, built-ins
  session.py      the multi-security LOB session (the join point)
  tasks.py        task specs and deterministic scorers
  runner.py       multi-world runner, three partitions, validity, report, replay package
```

## Limitations

- The mechanism partition rotates through two held-out families; a third family
  would need to be added to `MECHANISM_FAMILIES` and to the engine interface.
- One session clock tick per exchange command; there is no wall-clock latency
  simulation inside the slice.
- Marks are last-traded-price; an aggressively filled order can therefore show a
  small paper gain from multi-level fills.
- Market making and portfolio scorers are first-pass and not exhaustively
  adversarial; the adversarial suite covers round trips, overfill, invalid
  lifecycle spam, maker credit, agent unavailability, and process-family leakage.
- The background-agent population is a small, interpretable archetype set, not a
  calibrated participant mix.
