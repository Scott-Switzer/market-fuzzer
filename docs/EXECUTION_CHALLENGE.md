# Trade the Shock: execution challenge contract

`trade-the-shock` is the primary Quant Challenge Arena vertical slice. A learner configures a declarative policy to buy 6,000 fictional NOVA shares. The continuous price-time-priority exchange—not GPT or browser code—determines observations, orders, acknowledgments, fills, cancels, inventory, metrics, and ranks.

## Lifecycle and limits

The persisted lifecycle is:

```text
draft → public_practice → submission_locked → hidden_evaluation → released → archived
```

The default challenge allows five public practice runs and one final submission per learner. Practice scores are exact, only best-public display is enabled, and hidden evaluation is final-only. Drafts are stored separately from final submissions. Illegal transitions and out-of-phase actions return an error and create no new result. SQLite checks each practice/final limit and inserts the accepted record in the same immediate transaction, preventing concurrent requests from overshooting the limit in the supported single-database demo.

## Declarative policy `1.0`

The strict Pydantic contract accepts:

| Field | Accepted value |
| --- | --- |
| `schema_version` | exactly `1.0` |
| `strategy_type` | `twap`, `pov`, or `adaptive_pov` |
| `target_participation` | `0.01`–`0.20` |
| `max_participation` | target or higher, at most `0.30` |
| `max_spread_bps` | `1`–`50` |
| `urgency_curve` | `uniform`, `front_loaded`, `back_loaded`, or `adaptive` |
| `feed_latency_tolerance_ms` | `0`–`10,000` |
| `cancel_after_ms` | `10`–`10,000` |
| `completion_buffer_steps` | `0`–`20` |
| pause/budget flags | strict booleans |
| `rationale` | `50`–`2,000` characters |

Extra fields, non-finite values, maximum participation below target, and adaptive urgency on TWAP are rejected. No participant Python, shell command, package, URL, or arbitrary expression executes. Built-in TWAP, Aggressive POV, Guarded Adaptive POV, and Completion-first POV policies are immutable presets that use the same `_run_policy` exchange adapter as custom policies.

## Public practice

The public route accepts exactly one policy preset ID or one strict custom policy. It never accepts a world variant or an alternate public seed. The server loads `public_world_variant` from the stored challenge and uses exactly `PUBLIC_SEED = 42` for canonical public practice and public ranking.

Public score is deliberately legible:

```text
public score
= 10 × completion percentage
− max(implementation shortfall bps, 0)
− 2 × terminal inventory penalty
− 2 × max(maximum participation percentage − 25, 0)
```

Public practice returns the visible world hash, exact public metrics, and public replay. It does not return hidden world identifiers, parameters, hashes, replays, or scores.

## Hidden evaluation

Only an instructor in `submission_locked` can initiate evaluation. The server loads the protected variants from the challenge's persisted hidden-world manifest and the seeds from the internal `SEEDS` constant; neither is accepted from a learner request. The public score is not averaged over this protected seed set. The default contract is:

```text
public ranking:    normal public world × seed 42
protected matrix: four persisted protected worlds × seeds 41, 42
```

The protected implementation names and raw rows appear only in instructor evidence; the public brief exposes a count. Released student/model views receive overall aggregates plus coarse educational-intent aggregates named `thin_liquidity`, `message_latency`, `directional_crowding`, and `scheduled_event`. They never receive seed rows, internal world IDs, hashes, orders, fills, or protected replays. The instructor matrix includes every built-in benchmark and each stored final learner policy evaluated through the same `_run_policy` exchange adapter.

Each immutable record carries challenge/submission identity, policy version, exchange/agent/scoring version, world IDs loaded from the persisted manifest, world hashes, seeds, per-world metrics, aggregates, ranks, matrix hash, and stored timestamps. SQLite de-duplicates the completed challenge matrix. Release commits the visibility timestamp, phase transition, and audit entry atomically; it does not recalculate or mutate the matrix.

## Metric definitions

All quantities are computed from exchange records, not inferred from total market volume.

| Metric | Definition |
| --- | --- |
| Implementation shortfall | Direction-adjusted difference between strategy average execution price and arrival price, in basis points; unfilled inventory is reported separately. |
| Completion | Strategy filled quantity divided by the 6,000-share parent quantity. |
| Terminal inventory | Parent quantity minus cumulative strategy fills. |
| Average participation | Cumulative strategy fills divided by total NOVA market volume in the run. |
| Maximum participation | Maximum across steps of strategy-filled quantity at the step divided by total NOVA market volume at the step. |
| Participation violations | Count and first step where measured step participation exceeds the policy maximum. |
| Temporary impact | Direction-adjusted near-execution price displacement reported by the deterministic simulator. |
| Persistent impact | Direction-adjusted terminal-price displacement from arrival. |
| Inventory risk | Mean remaining parent quantity across uniformly spaced simulation steps. Terminal inventory is separately included in the completion component. |
| Adverse selection | Direction-adjusted terminal price movement relative to average execution price. |
| Order hygiene | Submitted, cancelled, rejected, active-at-horizon, active-after-completion, during-halt, concurrent-active, resting-duration, and cancel/fill evidence. It is reported but omitted from score because the current benchmark policies are market-order dominant and do not provide a comparable resting-order quality signal. |

Per step, the engine records submitted, cancelled, expired, rejected, filled, active child, market volume, observed volume, remaining parent, filled inventory, account inventory, participation, and shortfall contribution.

Hard invariants are checked at every step:

```text
submitted child quantity
= filled + cancelled + expired + rejected + active child quantity

parent quantity = cumulative filled quantity + remaining parent quantity
account inventory = signed cumulative strategy fills
```

## Latency and queue evidence

The coarse deterministic latency profile separates:

```text
feed_ms
decision_ms
order_entry_ms
cancel_ms
```

Each applicable record includes market-event, publication, observation, decision, submission, exchange-arrival, acknowledgment, fill, cancel-request, and cancel-effective times. Tests enforce monotonic lifecycle ordering; a fill cannot precede arrival and a cancel cannot become effective before its request.

The queue model is labeled `price_time_priority_simplified`. For a resting strategy limit order, replay exposes displayed quantity ahead at entry, quantity traded at that price before fill, and the partial-fill sequence. A market taker is explicitly `not_applicable_taker_order`; the UI does not invent a queue position.

## Robustness rubric

The current score decomposition totals 100 available points:

```text
30  mean hidden implementation shortfall
15  worst hidden implementation shortfall
10  temporary impact
15  completion and terminal inventory
15  participation discipline
10  mean remaining-parent inventory risk
 5  stability of shortfall across worlds/seeds
```

Mean and worst shortfall are normalized against a declared 250-basis-point bound. Impact uses a 450-basis-point bound. Completion combines mean completion (75% of that component) with terminal-inventory penalty (25%). Participation combines measured maximum participation (70%) and violation count (30%). Inventory risk uses mean remaining parent quantity against the 6,000-share order. Each component is stored in `score_decomposition`. `order_hygiene` is `null` and contributes zero because it is not comparable for the current market-order-dominant policies; there is no decorative constant. If the challenge later supports comparable resting-order tactics, it must add a real normalized component and explicitly version the scoring contract.

The default expected relationship is tested, not hardcoded:

```text
Aggressive POV: public rank 1
Guarded Adaptive POV: robustness rank 1
```

Ranking derives from score values. Regression tests use the production seeds and additional fixed seed groups so the teaching result cannot depend on one lucky run.

## Release views

Before release, a student sees public results and `withheld_until_release`. After release, a student may see allow-listed aggregate ranking fields and the coarse educational-intent aggregates described above. The feedback evidence package combines those fields with bounded stable IDs derived from that student's public event, trade, fill, and replay traces; it does not copy raw protected rows into the student view. This lets feedback discuss measured latency, depth, directional-flow, and scheduled-event mechanisms without disclosing seed rows or internal world identifiers. A validated or deterministic-fallback report is persisted and recovered rather than regenerated on a later request or restart. Student views never include raw per-world results or world hashes. Instructor raw world evidence and audit history remain protected after release.

## Challenge quality report

Protected evaluation stores an instructor-visible measured report. `challenge_behavior = PASS` means all of these checks passed: the thin-liquidity test reduced mean displayed depth, the message-latency test increased order-entry delay, the directional-crowding test increased forced sell flow, the scheduled event activated, every evaluated result preserved accounting, and an identical world/policy/seed/version replay reproduced the same result hash.

Each world also reports selected synthetic diagnostics for spread and depth distributions, return volatility, lag-one volume clustering, depth/spread correlation, and price response to signed flow. They are diagnostic measurements only; `REPORTED_NOT_CALIBRATED` explicitly prevents them from being presented as real-market calibration.

## GPT-authored design boundary

An instructor may request a structured educational design draft using allow-listed intervention intents and policy-parameter IDs. SQLite persists the qualitative draft and audit event for review. The draft cannot contain numeric world parameters, seeds, prices, quantities, latencies, metrics, scores, ranks, or claimed outcomes, and it does not create or mutate the numeric world manifest. Deterministic application code remains the only world-construction authority.

## Interpretation boundary

The result is a reproducible classroom assessment in a declared fictional exchange. Mechanical validity and selected synthetic diagnostics do not establish real-market calibration, future profitability, best execution, production capacity, live-trading safety, or regulatory compliance.
