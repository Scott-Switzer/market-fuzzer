# Trade the Shock: execution challenge contract

`Trade the Shock` is the primary Quant Challenge Arena vertical slice. A participant selects a declarative execution policy; the internal continuous double auction, not GPT or the UI, determines every order, fill, account movement, and score.

## Public practice

The visible world buys 6,000 fictional NOVA shares with normal displayed liquidity, stable background flow, and low exchange latency. Public practice score is completion first, then lower non-negative implementation shortfall:

```text
public score = 10 × completion percentage − max(shortfall bps, 0)
```

This intentionally leaves room for a policy that looks strong publicly to lose after hidden evaluation.

## Hidden evaluation

The same base exchange and seed family receive controlled interventions:

- liquidity withdrawal;
- forced seller and momentum crowding;
- NOVA earnings shock;
- high exchange-latency shock.

The robust score is a transparent relative challenge rubric, not a production estimate. It weights mean shortfall (60%), completion (5%), temporary impact (10%), terminal inventory (5%), worst-world shortfall (15%), and order hygiene (5%). It is computed only after deterministic simulations finish.

## Boundaries

- No arbitrary participant code executes.
- The public route does not disclose hidden-world parameters or benchmark outcomes.
- The prototype uses fictional structural worlds; real-market calibration is explicitly **not claimed**.
- A challenge failure is evidence about the declared synthetic environment, not evidence of future profitability, live-trading safety, or regulatory compliance.

## Reproduction

The instructor matrix records the challenge/schema, exchange and agent-population versions, scoring version, policy identity, seed list, world variants, world hashes, and a matrix hash. `tests/test_execution_arena.py` verifies a real public run, role-scoped hidden evidence, and the public-versus-hidden ranking reversal.
