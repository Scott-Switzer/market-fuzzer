# Architecture
```text
User-acquired sample or deterministic aggregate demo
  -> aggregate-only CalibrationPackV1 + accepted parameter ensemble
  -> emergent_calibrated WorldSpec + explicit interventions
  -> QueueReactiveProvider + heterogeneous agents
  -> exact price-time-priority exchange (sole state authority)
  -> common-random-number participation campaign
  -> five-vector SimulatorValidationReport + claim gates
  -> SyntheticReleaseValidationReport
  -> hashed SyntheticMarketPackage
```

The deterministic engine is authoritative for prices, orders, fills, accounting, and metrics. GPT-5.6 is isolated to compilation and assumption disclosure. The internal backend is always available; research-system adapters are optional future extensions.

Key boundaries:

- `app/schemas/`: versioned contracts and hashes
- `app/calibration/`: chronological aggregate compilation and bounded bootstrap calibration
- `app/orderflow/`: swappable rule-based and queue-reactive providers
- `app/exchange/`: exact order lifecycle, matching, and settlement
- `app/agents/`, `app/simulation.py`, `app/world/`: world execution
- `app/experiments/`: batch orchestration and artifacts
- `app/analytics/`: paired-seed claim gates and failure analysis
- `app/validation/`: fit-for-use and confidentiality/derivation reports
- `app/compiler.py`: offline/GPT compilation
- `app/api/`, `app/cli.py`, `app/static/`: product surfaces
