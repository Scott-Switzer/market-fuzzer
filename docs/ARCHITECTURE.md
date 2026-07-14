# Architecture

```text
Prompt -> deterministic compiler -> WorldSpec -> macro + issuers + events
      -> heterogeneous agents -> price-time-priority order books -> strategy fills
      -> scenario battery -> browser report
```

`WorldSpec` is the reproducibility contract. The engine owns prices, fills, depth, accounting, macro state, issuer fundamentals, and agent decisions; the prompt compiler only maps language into explicit parameters. An eventual GPT-5.6 compiler will be constrained to the same schema and validated before execution.
