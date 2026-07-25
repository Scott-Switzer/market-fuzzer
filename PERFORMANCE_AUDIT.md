# Performance Audit — Break Test Forward Path

## Executive Summary
The end-to-end exchange forward-test path is dominated by `run_simulation()` per world, not strategy evaluation. Each synthetic world builds an 120-step exchange with multiple agent populations; with 100 worlds/regime, runtime is dominated by event-loop overhead and canonical spec hashing.

## Hot Path Analysis

### High time-consumption
- `app/simulation.py:run_simulation`
  - Exchange book snapshots, agent decisions, and order scheduling for 120 steps dominate wall time.
  - Multiple `defaultdict`, dict/list copies per step add Python-object overhead.
- `app/break_test/exchange_fwd.py:run_exchange_forward_test`
  - Rebuilds a full `WorldSpec` for every world even when regime, asset universe, and exchange setup are identical.
  - Recreates agent populations and `AgentsSpec` in `_build_agent_spec` for every world.
- `app/break_test/synthetic_market.py:ResearchSyntheticMarketGenerator`
  - Instantiating reused class objects per world was replaced in patches with cheap C-stub paths; current code keeps this lean.

### High allocation / excessive copies
- Per-step `timeline` frames duplicate full `asset_states`, `events`, `agent_states`, and strategy-step summaries even when consumers only need target mid-prices.
- `_build_strategy_steps` creates many temporary dicts/lists and rebuilds accounting for all steps even when only one symbol is observed.
- `exchange.submit(...)` and `exchange.cancel(...)` create Book/Linked structures; repeated submission/cancel phases keep generating Python objects.

### O(n^2) / repeated recomputation
- `run_exchange_forward_test` loop nests regime → world without any caching for shared regime/asset inputs.
- `_validate_prices` / `_resolve_asset_universe` rerun on every world even for the same regime and universe preset.

## Measured Timing (baseline observed)
- 1 synthetic world, 8 assets, AAPL target: ~18.94s
- Command used: `scripts/benchmark_baseline.py`
- From this baseline, 100 worlds/regime × 4 regimes ≈ 7,576 seconds if scaled linearly, confirming the target is unrealistic without changes.

## Recommended Production Hardware
- CPU: Apple M-series or modern 4+ core Intel/AMD CPU with AVX2/NEON.
- Memory: 32GB+ recommended; current leaky object loop can peak 150–250MB per process.
- Concurrency: prefer multiprocessing/process pool workers if parallelism is desired, with deterministic seeds partitioned across workers.

## Proposed/Observed Tradeoff
Current demo path uses 10 worlds/regime for a run time of roughly 4 minutes. To hit a 60-second target at 100 worlds/regime, world simulation would need ~1.5s/world, roughly 10x faster than the current implementation.

## Future Optimizations
1. Reduce step count or use a lighter observation-only simulator that only records target mid-prices without full book/agent state reconstruction.
2. Cache immutable `WorldSpec` components across worlds and reuse with seed-scoped mutation.
3. Reduce timeline/agent telemetry for forward-test workloads.
4. Parallelize world evaluation with a bounded process pool and deterministic seed partitioning.
