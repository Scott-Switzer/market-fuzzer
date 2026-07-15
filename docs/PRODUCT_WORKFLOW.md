# Product workflow

## Primary: Execution Challenge Arena

```text
Instructor opens challenge
→ student configures declarative execution policy
→ public practice in the visible synthetic exchange
→ draft or immutable final submission
→ instructor locks and evaluates server-selected protected worlds
→ instructor releases allow-listed aggregates
→ student compares public rank with robustness rank
→ replay and GPT-5.6 or deterministic fallback explain verified evidence
```

The learner edits participation, spread, urgency, latency tolerance, cancellation, completion-buffer, halt, and pending-budget controls. Arbitrary participant code is out of scope. The server generates and resumes the demo identity that owns practice and submission state. Public practice is limited and uses the stored public world at seed `42`; the final policy is persisted; and hidden evaluation uses the persisted protected-world manifest with `SEEDS = (41, 42)` to create one immutable matrix. Requests cannot substitute a world or protected seed.

The product returns synchronized market and strategy replay: price/spread/depth/events, submissions/cancels/fills, inventory, participation, latency lifecycle, queue-ahead evidence, and per-step shortfall contribution. Released feedback uses aggregate evaluation metrics plus stable IDs from the learner's public trace; its persisted report is recovered after reload or restart. Raw protected world evidence remains instructor-only. Deterministic code owns every outcome.

An instructor may also ask GPT-5.6 for a qualitative educational design draft. The application persists that draft for review, but only approved intent IDs are allowed and no numeric world is created. The deterministic challenge manifest remains unchanged until application code explicitly defines a world.

## Advanced: Market Fuzzer

`/market-fuzzer` preserves the protected developer-tool workflow:

```text
Strategy → Safety Properties → Baseline → Break My Strategy
→ Counterexample → Minimize → Fix and Retest → Regression Test
```

It provides a fragile POV tutorial, bounded adverse search, seed reproduction, minimization, passing neighbor, exact corrected comparison, replay, and YAML/JSON regression fixtures. It is intentionally secondary to the learner assessment flow.

## Secondary: research/positions challenge

The older generated-panel and `date,asset,position` challenge remains available through `/api/arena/challenges/...` for experimental research teaching. It is not the homepage, execution leaderboard, or five-minute judge path.

## Evidence boundary

All three tools report behavior only inside their declared fictional deterministic environments. They do not prove profitability, market prediction, production capacity, institutional calibration, live-trading safety, or regulatory compliance.
