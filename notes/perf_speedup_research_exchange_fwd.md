# Performance / Speedup Research — exchange forward path

Measured baseline comes from `PERFORMANCE_AUDIT.md` and current sources.

## Baseline
- `run_simulation(...)` single world ≈ **18.94s–19s**
- Current demo workload: **40 worlds** (`10 worlds/regime × 4 regimes`)
- 100 worlds/regime linear projection: **≈ 7,600s** for 4 regimes; audited as infeasible.

## Speedup Levers Examined

### 1. Process-pool parallelization
- Where: `app/break_test/exchange_fwd.py:run_exchange_forward_test` outer regime/world loop, and `app/break_test/exchange_fwd.py:run_exchange_forward_test` inner `world_idx` loop or a per-regime fan-out.
- Constraint: each world is independent (`spec.seed` partitions). `run_simulation` has no global mutable state.
- Hardware: measured 8 logical cores on this machine.
- Theory: CPU-bound Python can approach **N-1 speedup** with `ProcessPoolExecutor` on N cores.
- Practical estimate on macOS arm64 with object-heavy simulation: **~6×–7×** effective speedup.
- Expected wall time for 100 worlds/regime:
  - without parallelism: 7,600s
  - with 8-way parallelism: **≈ 1,100s–1,300s** total
  - per regime: **≈ 275s–325s**
- Verdict: necessary but not sufficient for 60s/regime target.

### 2. Immutable-world caching
- Where: `app/break_test/exchange_fwd.py:run_exchange_forward_test` recreates `_resolve_asset_universe` and `build_world` every iteration even when `regime_key`, `asset_count`, `universe_preset`, and `strategy_asset` are identical.
- Cheap fix: pre-build one immutable `WorldSpec` prototype per regime/universe, then clone + mutate `seed`. Exchange-fwd later mutates `experiment.target_asset` only.
- Fraction of runtime saved: world spec construction is dominated by `run_simulation` (~19s). Spec build is on the order of 50ms, so savings are **<1%** of wall time.
- Exact line targets:
  - `app/break_test/exchange_fwd.py:663-675`
  - `app/break_test/exchange_fwd.py:312-380` has cheap `_build_agent_spec` recreation per world too.
- Verdict: worthwhile for determinism and load reduction, but alone it only punches ~0.5s off 19s.

### 3. Timeline-frame / output pruning
- Where: `app/simulation.py:run_simulation` builds `timeline`, `agent_states`, `orders`, `trades`, `cancels`, `events`, `strategy_steps`, `strategy_observations` regardless of caller need.
- Current forward workload consumes only `sim.trades`, `sim.timeline[asset_states][target][mid_ticks]`, and `sim.agent_states` for rollup.
- Removing optional per-step telemetry would lower post-processing allocations but does not move the event-loop wall time, which is proportional to steps and agents, not result recording.
- Exact line targets:
  - `app/simulation.py:745-770` per-step `timeline` append
  - `app/simulation.py:760-770` per-step `agent_states` append
  - `app/simulation.py:870-900` full deterministic payload/hashing
- Verdict: memory and determinism win; simulation runtime unchanged.

## Combined Potential
| Lever | Expected runtime reduction | Notes |
|------|---------------------------|-------|
| Parallelism only | ~6×–7× | Dominant lever |
| Parallelism + caching | ~6.2×–7.2× | Marginal improvement |
| Parallelism + pruning | ~6×–7× | Not simulation-time |
| All three | ~6.5× effective | Realistic for 8-core arm64 |

To hit **60s/regime at 100 worlds/regime** requires ≈ 1.5s/world. Parallelism alone gets to ~2.75s–3.25s/world. Additional 2×–3× further reduction would need algorithmic changes (step reduction, lighter sim, or compiled matching).

## Exact File Targets, Line-Level Changes

### Target 1: `app/break_test/exchange_fwd.py`
- `run_exchange_forward_test` outer loop (`635-744`)
  - Replace sequential `for regime_index, regime_key in enumerate(regime_keys)` with a process-pool dispatch for per-world jobs.
  - Pre-build one immutable `WorldSpec` prototype per regime + universe via `build_world(...)` once into an LRU/namedtuple cache, then clone with `dataclasses.replace` / manual factory using only new `seed`.
  - Move `_resolve_asset_universe(...)` above the regime loop and cache by `(regime_key, asset_count, universe_preset, universe_csv)`.
  - Move `UserStrategyOrderRouter` construction only if `forward_execution_mode == "real"`, still cheap.

### Target 2: `app/simulation.py`
- `run_simulation` (`253-900`)
  - Optional `Omit` kwargs: `collect_timeline`, `collect_agent_states`, `collect_strategy_steps` to skip per-step appends when not needed.
  - Exact regions to gate:
    - `281-283` initialize lists conditionally
    - `745-770` append loops
    - `870-900` deterministic dict construction

## Benchmark Command
Compare old vs new workload on a stress slice:

```bash
python -m scripts.decision_benchmark_smoke \
  --regimes steady_trend sideways_choppy high_volatility sudden_selloff \
  --worlds-per-regime 10 \
  --assets 8 \
  --workers 8
```

Fallback/adapter-specific probe:

```bash
python scripts/benchmark_baseline.py
# Current shell target in the repo already times 10 worlds/regime.
```

For deterministic regression proof on changed `run_simulation`:

```bash
python scripts/determinism_check.py
```

Regression check must verify `spec.specification_hash()` and result `runtime_ms` ordering relative to deterministic seed layout.
