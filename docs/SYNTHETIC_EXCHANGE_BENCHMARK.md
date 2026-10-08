# Synthetic Exchange Benchmark (M10.6.1 evaluator-private process registry)

A procedurally generated synthetic financial world + exchange + benchmark for
training and evaluating financial AI agents. This document describes the vertical
slice that joins the pieces already in this repository into one reproducible
synthetic trading session with sealed evaluation worlds.

M10.5 shipped one generator family. **M10.6 added a true mechanism-family
holdout**: three process families behind a common interface, a clean separation
between the *process family* and the *ecology*, the familiar / distribution /
mechanism evaluation partitions, and a market-process generalization gap.
**M10.6.1 closes the architecture** so that holdout can also be
*evaluator-private*: the families a campaign may use come from an explicit
`MarketProcessRegistry`, the campaign itself is an explicit immutable
`EvaluationPlan`, and every world carries a `DatasetSplit` that keeps sealed
evaluation worlds out of any future training corpus.

## What the slice joins

```
Evaluation plan  (app/benchmark/plan — partitions, family ids, ecologies, weights)
        +
Process registry  (app/benchmark/process_registry — family id -> definition,
                   public or evaluator-private)
        +
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
| `markov_regime_jump_factor_t_v1` | A hidden two-state Markov chain with regime-specific drift and volatility, plus a one-shot (Bernoulli) jump per session whose size is regime dependent. Stressed regimes jump harder. | held out (mechanism) |

Every family is **normalized to the same unconditional per-session variance per
node role**, so the family axis changes the *dynamics* of the path (volatility
clustering, tails, drift regimes), not its level. `volatility_scale` is a pure
scale that multiplies the unconditional variance by `scale²` without touching the
dynamics — this is what keeps the ecology axis orthogonal to the family axis.

## Process registry: public and evaluator-private families

`ProcessFamily` is the behavioural contract: `innovations(stream, sessions,
variable)` plus `unconditional_variance()`. The registry is how a campaign reaches
an implementation of it:

| Type | Role |
|---|---|
| `ProcessFamilyDefinition` | Names a family with a stable, versioned identifier (`<slug>_v<N>`) and builds one `ProcessFamily` per node role at a requested volatility scale. |
| `MarketProcessRegistry` | An explicit `family id -> definition` map. `register`, `resolve`, `contains`, `family_ids`, `public_family_ids`, `build`, `commitment`. |
| `default_process_registry()` | A fresh registry holding exactly the three published families. |

`ProcessFamilyKind` and `MECHANISM_FAMILIES` remain as convenience metadata for
the built-in families. They are no longer the type boundary: a test pins the enum
against `public_family_ids()` so the two cannot drift. Publishing a *fourth*
family means writing a definition, adding it to the public registry, and naming it
in a plan template; the enum member is metadata for the open benchmark, not a
requirement of the evaluation architecture.

An **evaluator-private family** is registered by trusted evaluator code:

```python
registry = default_process_registry()  # the public benchmark
registry.register(MyPrivateDefinition())  # from the evaluator's own process
report = run_benchmark(..., plan=my_sealed_plan, registry=registry)
```

The injection surface is one `register` call on an object the evaluator already
holds. Nothing is loaded dynamically: there is **no import path, no `eval`, no
module name from an HTTP request, and no environment variable naming a module to
import**. Resolution is by exact validated identifier, with no fallback — an
identifier the registry does not hold raises `UnknownProcessFamilyError` with the
code `UNKNOWN_PROCESS_FAMILY`. A sealed plan therefore *fails loudly* against the
public registry instead of silently degrading, and it fails **before any world is
generated**.

### Family commitments

`family_commitment(definition)` digests a family's role-normalized structure
(class identity plus its dataclass configuration at `volatility_scale = 1.0`). It
identifies the *generator*, not the ecology level a particular world ran it at, so
an authorized replay can prove which generator produced a sealed world without
publishing it. Commitments are computed once per registry.

## Evaluation plans

A campaign is an explicit, immutable value — not a policy buried in the runner:

```python
EvaluationWorldTemplate(partition, family_id, ecology_id, weight=1)
EvaluationPlan(plan_id, version, worlds=(template, ...), description="")
```

`worlds` is a **cycle**, not a complete list: world `i` uses template `i % n` of
the cycle, each template repeated `weight` times. One plan therefore describes a
12-world and a 32-world campaign. Resolving a plan against a
`MarketProcessRegistry` and an `EcologyRegistry` produces `PlannedWorld` values
carrying the world id, seed, partition, family visibility, public family label,
family commitment, resolved ecology, and split classification.

The runner consumes `EvaluationPlan + MarketProcessRegistry + EcologyRegistry` and
owns no benchmark-design policy of its own.

### The default plan: `m10_6_open_ood_v1` (v1)

```
FAMILIAR      gjr_factor_t_v1                  familiar-baseline
DISTRIBUTION  gjr_factor_t_v1                  distribution-shift
MECHANISM     stochastic_vol_factor_t_v1       distribution-shift
FAMILIAR      gjr_factor_t_v1                  familiar-baseline
DISTRIBUTION  gjr_factor_t_v1                  distribution-shift
MECHANISM     markov_regime_jump_factor_t_v1   distribution-shift
```

This six-template cycle is M10.6's `index % 3` partition rule with its
`(index // 3) % 2` mechanism rotation, stated explicitly. The default plan
reproduces the M10.6 world identities, seeds, partitions, generators, ecologies,
per-world ledger digests, market hashes, action digests, and scores **byte for
byte**. Only the replay-package digest changed: every world record gained the
provenance fields below, which is a superset of the M10.6 record.

### An evaluator-private plan

The test suite builds `sealed_test_plan_v1`, whose mechanism partition runs
`test_private_mean_reverting_family_v1` — an AR(1) level-reverting process that
implements `ProcessFamily`, is absent from the public enum and the public
registry, and is normalized to the published baseline's unconditional variance so
the sealed score reflects *structure* rather than a volatility shift. The same
plan fails with `UNKNOWN_PROCESS_FAMILY` against the public registry.

A campaign that contains sealed worlds is labelled:

| | Value |
|---|---|
| Public rendering | `Evaluation plan: evaluator-private plan (v1)` |
| Evaluator-side `BenchmarkReport` | the real `evaluation_plan_id` |
| Replay package | the real `evaluation_plan_id` and `evaluation_plan_version` per world |

## Public and evaluator-private metadata

The boundary is explicit and tested:

| Visible to | Content |
|---|---|
| **Agent** (`StrategyObservationV2`) | quotes, spread, the agent's own order state and inventory, task remaining quantity, session id, step. The protocol schema and nothing else — the field set is asserted against `StrategyObservationV2.model_fields` on a sealed world. |
| **Public result** (`BenchmarkReport.render()`) | task score, the three partition scores and gaps, the weakest environment, and a process-family label: the real family id for a published family, `evaluator-private` for a sealed one; `evaluator-private plan` instead of a sealed campaign's identifier. |
| **Evaluator private** (`BenchmarkReport.replay_package`) | the real family id and visibility, the family commitment, the plan id and version, the partition and split, the ecology, the seed and its commitment, the generator bundle digest, the market/ledger/action digests. |
| **Trusted evaluator code only** | the registry itself: private definitions, their classes, their parameters, and any private evaluator metadata they carry. |

Public families are not called secret. The default campaign is
**open-source process-family OOD**; a campaign that registers a private family is
an **evaluator-private mechanism holdout**. The two terms are not interchangeable,
and the public report says which one ran.

## Dataset splits and the M10.7 export gate

Every world carries a `DatasetSplit` derived from the *registry*, not from a
caller's intent:

| Split | Meaning |
|---|---|
| `TRAINABLE` | Generated explicitly for training; never used for official scoring. |
| `PUBLIC_EVAL` | An official evaluation world on a published family. |
| `SEALED_EVAL` | An official evaluation world on an evaluator-private family. |

`is_trainable(split)` is true only for `TRAINABLE`, and `require_trainable(splits)`
is the M10.7 export gate: it raises `SealedEvaluationExportError`
(`SEALED_EVAL_NOT_TRAINABLE`) for anything else. No exporter exists yet; the
gate and its tests exist now so the exporter cannot be written without confronting
it, and the split is recorded per world in the replay package so a downstream
exporter reads the classification the campaign committed to rather than
guessing.

The two held-out families also start in their stationary state: the
stochastic-volatility path draws its initial log-variance from the stationary
distribution of its latent process, and the regime-jump path draws its initial
regime from the stationary regime distribution. The regime-jump innovations are
mean-centred on the stationary mean, so that family carries no drift relative to
the zero-mean GJR and stochastic-volatility families. `gjr_factor_t_v1` is the
exception: it starts from a *fixed* variance equal to its unconditional variance,
so its expected variance is right from the first session but its short-horizon
*distribution* is not a draw from the stationary law. That is the M10.5 behaviour,
left unchanged.

`unconditional_variance()` equals the variance each family actually generates, and
a regression test checks the realized variance of every family at every ecology
level.

## Evaluation partitions

Each sealed world is assigned to exactly one partition **by the plan it belongs
to** (see [Evaluation plans](#evaluation-plans)); the default plan
`m10_6_open_ood_v1` uses:

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
  resolved from the evaluator's registry and recorded only in the evaluator-side
  run manifest and the replay package. Observations never expose the partition,
  the process family (public or private), the plan identity, the ecology label,
  the world index, the world seed, the split, or generator parameters. The agent
  session ID is an instrument-independent digest over evaluator-private seed
  material (`sx-<32 hex>`) and does **not** depend on the process family, so two
  worlds that differ only by their generator family share an agent-facing
  identity. Tests assert that no family, plan, or partition token can reach any
  serialized observation, that the agent-visible field set is exactly the
  published `StrategyObservationV2` schema on a sealed world, and that canaries
  planted in a private family's identifier, class name, and metadata — plus its
  plan's identifier and description — never appear in an observation or in the
  public rendering. A sealed run's public output names no private identifier at
  all; the real family id, its commitment, and the plan provenance remain in the
  evaluator's replay package, so redaction does not cost reproducibility.
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

`--worlds 32 --securities 8 --days 5 --steps-per-day 30` (1,719,614 exchange
events, 32/32 distinct per-world ledger digests, two runs byte-identical):

```
FINANCIAL WORLD FACTORY BENCHMARK

Agent: builtin-twap
Task: Optimal Execution
Evaluation worlds: 32 sealed synthetic worlds
Evaluation plan: m10_6_open_ood_v1 (v1)
Valid worlds: 32
Securities encountered: 8
Exchange events: 1,719,614

Completion                 97.4%
Implementation shortfall   121.4 bps
Tail shortfall            534.8 bps
Peak inventory exposure   50000
Constraint violations         0

Familiar worlds score       80.6  (11 valid worlds)
Distribution worlds score   67.4  (11 valid worlds)
Mechanism worlds score      81.9  (10 valid worlds)

Distribution generalization gap      -13.2
Mechanism generalization gap          +1.3
Process-family generalization gap    +14.5

Weakest environment:
distribution / gjr_factor_t_v1 / distribution-shift (world bench-0025-distribution)

Replay package: 32 worlds, digest 3650ff0d9f3359bf68b6cc7a5ed4ab85

Validity: VALID
```

Every number above is identical to the M10.6 run of the same campaign, including
all 32 per-world ledger digests, market hashes, action digests, and scores; the
replay-package digest changed only because each world record now carries the plan
id and version, the split, the family visibility, the family commitment, and the
seed and generator commitments. (M10.6 printed
`Replay package: 32 worlds, digest 262bc7e638374b94…`.)

For this non-modeling TWAP baseline the isolated process-family gap is *positive*:
it does not overfit the familiar generator. The two held-out families are still
scored apart from each other (per-world means ≈73.8 for the regime-jump family and
≈90.0 for stochastic volatility), which is direct evidence that the instrument
resolves generator-dependent behaviour rather than injecting family noise.

## Training corpus (M10.7)

The same world machinery now has a guarded *training* side. Every
`EvaluationWorldTemplate` declares its `DatasetSplit` (there is no inference
from family visibility), the `training` partition joins the three evaluation
partitions, and the published plan `m10_7_training_v1` declares every world
`TRAINABLE` across all three public process families and both published
ecologies -- familiar-baseline and distribution-shift.

The two paths are mutually explicit:

* the **benchmark** raises `TRAINABLE_PLAN_IN_BENCHMARK` if a plan contains
  TRAINABLE worlds;
* the **corpus builder** raises `SEALED_EVAL_NOT_TRAINABLE` (the M10.6.1 gate,
  now covering any non-TRAINABLE world) before generating, building a port, or
  touching the filesystem when a plan contains PUBLIC_EVAL or SEALED_EVAL
  worlds.

A successful corpus build is an immutable, hash-committed Apache Parquet
release under the `fwf-corpus-v1` contract: seven tables (episodes, securities,
daily bars, agent decisions, exchange commands, exchange events, decision-time
aggregate L10 book snapshots), a `SessionRecorder` observation seam on the
session, a streaming bounded-memory sharded writer (ZSTD, deterministic
`part-NNNNN.parquet` names), per-table logical SHA-256 hashes plus per-shard
physical hashes, a non-environmental release digest, and an independent
validator (`fwf corpus validate`) that re-derives every claim from the persisted
bytes.

```bash
python -m app.cli corpus build --output artifacts/corpora/m10-7-smoke \
  --task execution --policy twap --worlds 32 --securities 8 \
  --days 5 --steps-per-day 30 --seed 20261007
python -m app.cli corpus validate artifacts/corpora/m10-7-smoke
```

Full format, schema, guarantees, scale numbers, and limitations:
[SYNTHETIC_TRAINING_CORPUS.md](SYNTHETIC_TRAINING_CORPUS.md).

## Layout

```
app/market/
  process.py      ProcessFamily interface + stochastic-volatility and regime-jump families
  engine.py       factor engine; GjrGarchT is the gjr_factor_t_v1 family
app/corpus/
  schema.py       explicit fwf-corpus-v1 Arrow schemas (seven tables, grains)
  recorder.py     SessionRecorder seam protocol + null default
  writer.py       bounded buffered -> ZSTD sharded Parquet + streaming logical hash
  builder.py      the guarded build path, recorder sink, manifest, dataset card
  validate.py     independent validator with stable failure codes
```

## Limitations

- `ProcessFamilyDefinition.build` is called for every node of every world, and
  once per role when a commitment is first computed. A family whose construction
  is expensive would want the definition to memoize; the published families are
  frozen dataclasses and do not.
- A family commitment digests class identity plus dataclass configuration. A
  family that keeps its parameters in non-dataclass attributes contributes only
  its class identity, so two such variants would commit alike — implementations
  that want full provenance should be dataclasses.
- The registry is a mutable object handed to trusted evaluator code. It is not a
  sandbox: an evaluator that registers a family can run it, which is the point.
  What the architecture removes is the requirement that every evaluated family be
  *published*.
- Redaction covers the public rendering and the agent boundary. `BenchmarkReport`
  itself is the evaluator's artifact and holds the real identifiers; code that
  serialized the whole report to a participant would leak them.
- M10.7 closes the split loop: the exporter exists, the training plan shipped,
  the session records non-intrusively, and the corpus CLI validates releases.
  Remaining corpus limitations (logical clock, archetype agents, decision-time
  snapshots only, reference exchange) are listed in
  [SYNTHETIC_TRAINING_CORPUS.md](SYNTHETIC_TRAINING_CORPUS.md).
- One session clock tick per exchange command; there is no wall-clock latency
  simulation inside the slice.
- Marks are last-traded-price; an aggressively filled order can therefore show a
  small paper gain from multi-level fills.
- Market making and portfolio scorers are first-pass and not exhaustively
  adversarial; the adversarial suite covers round trips, overfill, invalid
  lifecycle spam, maker credit, agent unavailability, and process-family leakage.
- The background-agent population is a small, interpretable archetype set, not a
  calibrated participant mix.
