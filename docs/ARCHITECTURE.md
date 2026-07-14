# Architecture
```text
Natural-language request
  -> Offline rules or GPT-5.6 structured output
  -> strict WorldSpec validation + canonical SHA-256 hash
  -> synthetic macro, issuers, fundamentals, information events
  -> heterogeneous agents + latency queue
  -> exact price-time-priority continuous double auction
  -> strategy trades, ledgers, snapshots, and agent state
  -> common-seed scenario battery
  -> execution metrics, component realism, failure surface
  -> self-contained artifacts + browser/CLI/API results
```

The deterministic engine is authoritative for prices, orders, fills, accounting, and metrics. GPT-5.6 is isolated to compilation and assumption disclosure. The internal backend is always available; research-system adapters are optional future extensions.

Key boundaries:

- `app/schemas/`: versioned contracts and hashes
- `app/exchange/`: exact order lifecycle, matching, and settlement
- `app/agents/`, `app/simulation.py`, `app/world/`: world execution
- `app/experiments/`: batch orchestration and artifacts
- `app/analytics/`: measured diagnostics and failure analysis
- `app/compiler.py`: offline/GPT compilation
- `app/api/`, `app/cli.py`, `app/static/`: product surfaces
