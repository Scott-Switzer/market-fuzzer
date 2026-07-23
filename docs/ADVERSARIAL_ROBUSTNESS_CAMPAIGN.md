# Adversarial Robustness Campaign Mechanism

## 1. Failure Taxonomy

Failures are classified by projection, observable quantity, and adversary intent. This maps directly onto the existing `run_search` candidate grid, `_adversarial_mutation`, regime catalog, and `DEFAULT_PROPERTIES` threshold predicates in `app/`.

### Categories

| ID | Projection | Observable Quantity | Canonical Predicate | Example |
|-----|------------|---------------------|---------------------|---------|
| F-PART | Execution participation | `max_participation_pct` | `< threshold %` | Fragile POV exceeds 12% participation cap |
| F-SHORT | Execution cost | `implementation_shortfall_bps` | `<= threshold bps` | Shortfall exceeds 20 bps |
| F-COMP | Fill completion | `completion_pct` | `>= threshold %` | Completion below 95% |
| F-INV | Parent inventory | `remaining_inventory_pct` | `<= threshold %` | More than 5% unfilled |
| F-HALT | Control compliance | `orders_during_halt` | `<= 0` | Orders submitted during halt |
| F-REG | Strategy regime sensitivity | `loss_rate_pct`, `worst_drawdown_pct` | `loss_rate_pct >= 60`, drawdown gates in `validation_quality_score` | SMA crossover loses money in sideway/choppy |
| F-STAB | Parameter instability | `robustness_score`, `score_spread` | `score_spread < 15` | Parameter sensitivity destroys Sharpe |
| F-ADV | Adversarial mutation degradation | `adversarial_oos_sharpe`, `adversarial_drawdown` | sharpe/drawdown deltas beyond threshold | Earnings-shock mutation collapses performance |
| F-COST | Transaction cost overflow | `total_bps` from `compute_turnover_cost` | `<= threshold` | Turnover-cost model exceeds budget |
| F-QUEUE | Queue accounting | `accounting_all_steps`, `order_hygiene_scored` | all-true / score threshold | Resting-order hygiene unverifiable under adversarial flow |
| F-REP | Replay/reconstruction | `inventory_accounting_ties` | `true` | Accounting ties break under mutation |

### Failure Family Labels
- **Liquidity / Depth Contraction**: depth reduction, liquidity withdrawal, forced-seller flow.
- **Latency / Temporal Skew**: delayed observations, async cancel races, latency shock.
- **Adverse Selection / Toxic Flow**: crowded unwind, adverse flow, order cancellation floods.
- **Regime Regime Shifts**: sudden selloff, high-vol Markov transition, crisis vol.
- **Cost Explosion**: spread widening, temporary/persistent impact, borrow fee spike.

**Target module/function**: `app/break_test/reporting.py:_build_failure_analysis`, `app/break_test/reporting.py:_identify_vulnerability`, `app/product.py:DEFAULT_PROPERTIES`.

---

## 2. Search Algorithm Recommendation

### Baseline: Deterministic Bounded Grid + Coordinate Descent Minimization
**Why not pure random / genetic search**: this repository is deterministic-first. Primary evidence must be reproducible from `world_id`, `seed`, and optional calibration parameter set. Random or evolutionary search destroys that contract.

**Recommended algorithm family**:
1. **Stage A — Sobol or Latin Hypercube coverage over declared parameter coordinates**  
   Use `numpy`-style fill over the discrete parameter ranges defined in `app/break_test/quant_validation.py:_build_candidate_grid` or the continuous ranges in `app/evaluation/sealed_v1.py:HiddenParameterRangeV1`. For continuous hidden ranges, use Sobol sequences via `scipy.stats.qmc.Sobol` or a small hand-rolled rank-1 Latin-Hypercube. This gives quasi-uniform coverage rather than the current corner-only `_build_candidate_grid` behavior.

2. **Stage B — Falsification by threshold predicate**  
   Evaluate each candidate against `DEFAULT_PROPERTIES` and any signed scoring policy digest. Only candidates crossing a failure rate threshold (`required_failures / seeds_tested`) qualify. This is the existing `_target_fail` logic in `app/product.py`; upgrade it to accept any predicate ID, not just `participation`.

3. **Stage C — Metamorphic / Property-Based Check**  
   Before accepting a candidate as a counterexample, require a **passing neighbor** mutation: verify that a verified adjacent non-failing scenario still passes. This is already present as `passing_neighbor` in `app/product.py` and is the local metamorphic relation. Extend it to all failure categories.

4. **Stage D — Counterexample Minimization / Delta Debugging**  
   Coordinate-descent reduction over the failing scenario's coordinates, preserving the threshold violation. This is exactly the pattern in `app/product.py:run_search` and should be generalized.

**Don't recommend**: genetic search, simulated annealing, or LLM search as the primary falsification engine. Those are acceptable only as a secondary hypothesis generator feeding deterministic verification.

**Target module/function**: `app/break_test/quant_validation.py:sensitivity_analysis`, `app/product.py:run_search`, `app/break_test/oos_validation.py:_adversarial_mutation`.

---

## 3. Deterministic Budget

### Definition
The deterministic budget is the maximum work performed in a single campaign before the engine commits to a verdict. It must be small enough to re-run from scratch on a different machine with identical inputs, and bounded so the system cannot claim exhaustive coverage.

### Required Budget Inputs

| Input | Source | Why |
|-------|---------|-----|
| `seeds` | `app/execution_arena.py:SEEDS`, `app/break_test/service.py:_SESSION_STORE` | Reproduction fidelity |
| `worlds_per_regime` / `worlds_per_family` | `app/break_test/regimes.py:build_world_price_path`, `app/evaluation/sealed_v1.py:WorldPlanV1` | Coverage depth |
| `parameter_set_count` | `app/experiments/runner.py:run_validation_campaign` | Parameter sweep breadth |
| `participation_steps` | `app/experiments/runner.py:participations` | Execution sensitivity |
| `max_combinations` | `app/break_test/cross_val.py:combinatorial_purged_cross_validation` | CV exhaustiveness cap |
| `candidate_grid_cap` | `app/break_test/quant_validation.py:_build_candidate_grid` | Hard cap on grid expansion |
| `n_bootstrap`, `n_trials` | `app/break_test/metrics.py`, `app/break_test/oos_validation.py` | Statistical sample size |
| `max_folds` | `app/break_test/oos_validation.py:_walk_forward_folds` | Walk-forward budget |

### Recommended Budget Contract

```python
CAMPAIGN_BUDGET = {
    "seeds": (41, 42),               # immutable seed contract
    "worlds_per_regime": 30,         # from robustness_product / regimes
    "parameter_sets_max": 10,        # accepted calibration sets in validation campaign
    "participations": (0.02, 0.05, 0.10, 0.20),
    "world_variants": ("normal", "liquidity_withdrawal", "crowded_unwind", "earnings_shock", "latency_shock"),
    "max_candidate_grid": 32,        # current cap in _build_candidate_grid
    "cpcv_max_combinations": 16,     # current cap
    "oos_max_folds": 24,
    "bootstrap_n": 500,
    "n_trials_deflated_sharpe": None,  # infer from fold count
    "message_budget_bytes": 64 * 1024,
}
```

**Target module/function**: `app/execution_arena.py:SEEDS`, `app/break_test/oos_validation.py:_walk_forward_folds`, `app/experiments/runner.py:run_validation_campaign`, `app/break_test/cross_val.py:combinatorial_purged_cross_validation`.

---

## 4. Severity Score Formula

### Existing Formula
`app/product.py:severity` uses a linear additive model with equal weights over six dimensions: `liquidity`, `volatility`, `latency_ms`, `forced_seller`, `spread`, `replenishment`. It is documented explicitly as nonstandard. That is acceptable for a *diagnostic* score, but not as an institutional severity taxonomy.

### Recommended Replacement Severity Policy

Use a weighted, bounded `[0, 1]` scalar composed of normalized and capped components:

```
S = w1 * I(liquidity < 1.0)                # depth contraction
  + w2 * I(volatility > 1.0)                # vol spike
  + w3 * max(0, (latency_ms - 10) / 90)     # latency skew
  + w4 * (forced_seller / 50000)            # adverse flow
  + w5 * max(0, (spread - 1) / 3)           # spread widening
  + w6 * I(replenishment < 1.0)             # liquidity non-replenishment
```

with weights summing to 1.

**Design requirement**: every component has a named rationale in the repo's failure taxonomy:
- F-LIQ → `liquidity`, `replenishment`
- F-LAT → `latency_ms`
- F-ADV → `forced_seller`
- F-COST → `spread`, `volatility`
- F-REG → regime-level loss-rate + drawdown (handled separately as regime-severity vector)

### Regime Severity Vector
For strategy-level candidate scoring (`sensitivity_analysis`, `worst_case_attribution`), augment the scalar with a regime vector:

```
V = [loss_rate_i/100, abs(worst_dd_i)/100] for i in regimes
```

and a regime-risk scalar:

```
R_reg = mean(V) + 0.25 * max(V)
```

The combined severity used for ranking is then:

```
S_total = 0.6 * S_scenario + 0.4 * clip(R_reg, 0, 1)
```

**Target module/function**: `app/product.py:severity`, `app/break_test/quant_validation.py:_robustness_score`, `app/break_test/validation_quality.py:_regime_robustness_score`.

---

## 5. Minimization Algorithm

### Delta-Debugging Style Coordinate Reduction

Reuse and harden the coordinate-reduction loop already present in `app/product.py:run_search`:

1. Initialize `minimized = original_failing_candidate`, `severity_before = severity(minimized)`.
2. For each coordinate in deterministic priority order, iterate from the most severe toward baseline value.
3. Accept the trial only if:
   - `target_failures >= required_failures`, and
   - `severity(trial) <= severity_before`.
4. Else, reject and continue to next candidate value for the same dimension.
5. After coordinate pass, recompute `severity_before = severity(minimized)` and continue to next dimension.
6. Terminate when no trial in the current dimension improves toward baseline while preserving failure reproduction.

**Neighbor Verification**: accepts a candidate as a minimizing counterexample only if a verified neighboring scenario (e.g., `liquidity + 0.15`) passes all predicates across the stored seed set.

**Minimum not Minimum**: store `minimization_trace` as a list of accepted and rejected steps, with `severity_before`, `severity_after`, `seeds_failed`, `seeds_tested`. The result is *locally minimized within declared coordinates*, not globally minimal.

**Target module/function**: `app/product.py:run_search` minimization loop, specifically the block between the initial qualifying candidate selection and the construction of `passing_neighbor`.

---

## 6. Replay Artifact Contract

### Existing Contract
`app/execution_arena.py:_replay_payload` and `app/experiments/artifacts.py` already define:
- Orders, cancels, trades, events, agent states, book snapshots as parquet.
- Deterministic `world_id` including variant, policy id, and seed.
- Environment hash, spec hash, result hash.

### Required Canonical Shape

Every produced counterexample fixture must include:

```json
{
  "campaign_id": "adv-robust-<date>-<hash>",
  "scope": "adaptive_diagnostic",
  "budget": {...},
  "world": {
    "variant": "liquidity_withdrawal",
    "seed": 42,
    "spec_hash": "<sha256>",
    "result_hash": "<sha256>",
    "environment_hash": "<sha256>",
    "parameter_set_id": "<sha256>"
  },
  "strategy": { "id": "...", "type": "...", "parameters": {...} },
  "predicate": { "id": "participation", "threshold": 12, "observed": 14.3, "operator": "<=" },
  "reproduction": {
    "seeds_tested": [41, 42],
    "seeds_failed": 2,
    "failure_rate": 1.0
  },
  "minimized_scenario": { ... },
  "passing_neighbor_scenario": { ... },
  "severity": { "policy_version": "severity-2.0", "score": ..., "components": {...} },
  "minimization_trace": [ { "step", "dimension", "old_value", "trial_value", "accepted", "severity_before", "severity_after", "seeds_failed" } ],
  "replay": {
    "orders": [...],
    "trades": [...],
    "events": [...],
    "agent_states": [...],
    "book_snapshots": [...],
    "strategy_activity": [...],
    "evidence_rows": [...]
  },
  "evidence": {
    "scope": "adaptive_diagnostic",
    "result_digest": "<sha256>",
    "claim_boundary": "Adaptive, strategy-aware diagnostic evidence; not independently selected primary evaluation.",
    "mechanism": "adversarial_robustness_campaign_v1",
    "limitations": ["Results describe software behavior inside the configured deterministic synthetic harness.", "..."]
  }
}
```

**Target module/function**: `app/execution_arena.py:_replay_payload`, `app/experiments/artifacts.py:REQUIRED_FILES` + `safe_artifact_dir`, `app/evaluation/evidence_v1.py:adaptive_diagnostic_evidence`, `app/product.py:export_fixture`.

---

## 7. Safeguards Against False Exhaustive-Coverage Claims

These are mandatory. Every safeguard already has a structural anchor in the existing codebase.

### 7.1 Scope Labeling
No stored result may omit `evidence.scope`. The three legal values are:
- `development_fixture` — break-test / deterministic fixture.
- `sealed_primary` — only produced by `app/evaluation/sealed_v1.py:SealedCampaignEvaluatorV1.finalize_primary`.
- `adaptive_diagnostic` — failure search result; **cannot** be substituted for primary evidence.

### 7.2 Budget-Bounded Output
Campaign outputs must always record the full `CAMPAIGN_BUDGET` used. If a result is produced with a budget shorter than the configured maximum, that is explicit and acceptable; claiming "full coverage" when `candidates_tested < grid_cap` is forbidden at the API level.

### 7.3 Parameter-Set Isolation
Hidden parameter ranges are committed before artifact freeze (`app/evaluation/sealed_v1.py:CampaignCommitmentV1`) and revealed only after finalization. A robustness search must not mutate hidden ranges during minimization without a new commitment round.

### 7.4 Metamorphic Passing-Neighbor Requirement
A failure is considered validated only if a passing neighbor is independently verified. This is the metamorphic relation already encoded in `app/product.py:run_search`. Enforce it at the API layer for all failure fixtures.

### 7.5 Claim-Boundary Strings
Every response containing a failure result must include a literal claim boundary string. Examples in the repo: `app/decision_benchmark.py` ("Synthetic benchmark decision evidence; not a profitability or live-execution claim."), `app/evaluation/evidence_v1.py:development_fixture_evidence` ("Deterministic development fixture inside declared synthetic mechanisms; not sealed primary evaluation.").

### 7.6 Exhaustiveness Flag
`app/break_test/cross_val.py:combinatorial_purged_cross_validation` already returns `"exhaustiveness": len(combos) == len(cpcv_combinations(...))`. Enforce that any CPCV result with `exhaustiveness=False` must attach the field and suppress claims of "all combinations tested".

### 7.7 Data-Quality Guards
`app/break_test/data_loader.py:warn_on_short_history` and `survivorship_flag` already produce explicit warnings. Any robustness campaign on inputs flagged as short or survivorship-biased must annotate the result with those warnings streamed into `evidence.limitations`.

### 7.8 Numerical Hygiene
Any LLM-generated failure explanation is revalidated against allowed numerical references. See `app/analyst.py:_validate_grounding`. This prevents invented Sharpe ratios or containment-claim leakage in narrative sections.

---

## 8. Exact Module / Function Targets for Implementation

| Capability | File | Function / Symbol |
|-----------|------|-------------------|
| Threshold predicate definitions | `app/product.py` | `DEFAULT_PROPERTIES` |
| Scenario severity scoring | `app/product.py` | `severity` |
| Parameter grid generation | `app/break_test/quant_validation.py` | `_build_candidate_grid`, `_default_param_ranges` |
| Sensitivity sweep | `app/break_test/quant_validation.py` | `sensitivity_analysis`, `worst_case_attribution` |
| Adversarial mutation | `app/break_test/oos_validation.py` | `_adversarial_mutation` |
| Walk-forward / adversarial validation | `app/break_test/oos_validation.py` | `walk_forward_validation` |
| Purged CPCV baseline | `app/break_test/cross_val.py` | `purged_k_fold_cv`, `combinatorial_purged_cross_validation` |
| Regime catalog / path generator | `app/break_test/regimes.py` | `build_world_price_path`, `detect_regimes`, `run_forward_test` |
| Validation quality / threshold gates | `app/break_test/validation_quality.py` | `validation_quality_score`, `_regime_robustness_score`, `_penalty_score` |
| Break test service / failure report | `app/break_test/service.py` | `run_break_test`, `build_failure_report` via `app/break_test/reporting.py` |
| Counterexample minimization | `app/product.py` | `run_search` minimization block |
| Replay payload | `app/execution_arena.py` | `_replay_payload`, `_execution_metrics`, `_run_policy` |
| Public / hidden challenge matrix | `app/execution_arena.py` | `benchmark_matrix`, `_public_score`, `_robustness_decomposition` |
| Sealed campaign commitment | `app/evaluation/sealed_v1.py` | `SealedCampaignEvaluatorV1`, `HiddenParameterRangeV1`, `CampaignCommitmentV1` |
| Evidence scope boundaries | `app/evaluation/evidence_v1.py` | `EvaluationEvidenceV1`, `adaptive_diagnostic_evidence` |
| Failure analysis grounding | `app/analyst.py` | `evidence_package`, `deterministic_analysis` |
| Campaign batch runner | `app/experiments/runner.py` | `run_batch`, `run_validation_campaign` |
| Artifact directory / hash manifest | `app/experiments/artifacts.py` | `safe_artifact_dir`, `write_json`, `write_parquet`, `sha256` |
| Challenge design / hidden intents | `app/execution_challenge_designer.py` | `validate_execution_challenge_design`, `HiddenTestIntent` |
| Regression fixture export | `app/product.py` | `export_fixture` |
| Decision evidence / non-profitability boundary | `app/decision_benchmark.py` | `build_decision_change_benchmark` |

---

## 9. Implementation Sequence

1. Hard-code `CAMPAIGN_BUDGET` to `app/break_test/campaign.py` (new).
2. Introduce `FailureCategory` enum and `adversarial_robustness_search` function in `app/break_test/campaign.py`. Drive from the sealed `HiddenParameterRangeV1` family ranges plus Sobol/LHS fill.
3. Refactor `app/product.py:severity` into the weighted formula above. Keep old as `severity_legacy`.
4. Generalize `app/product.py:run_search` minimization loop into `app/break_test/campaign.py:minimize_counterexample`.
5. Extend `app/execution_arena.py:_replay_payload` to emit the canonical replay artifact contract.
6. Enforce evidence-scope checks at `app/api/app.py` when returning `/api/break-test/session/{id}` and new `/api/robustness/campaign` routes.
7. Add exhaustive flag checks and `negative_guard_warning` to campaign summary results.
8. Propagate `data_loader.warn_on_short_history` warnings into `evidence.limitations` for every campaign result.

---

## 10. Example API Shape (Target)

```http
POST /api/robustness/campaign
Content-Type: application/json

{
  "campaign_id": "adv-robust-20260722T000000Z-a1b2c3",
  "strategy_id": "pov_fragile",
  "properties": ["completion", "shortfall", "participation", "halt", "remaining"],
  "seeds": [41, 42],
  "budget": {
    "worlds_per_regime": 30,
    "max_candidate_grid": 32,
    "oos_max_folds": 24,
    "cpcv_max_combinations": 16
  },
  "hidden_parameter_ranges": [
    {"family_id": "market", "parameter_name": "liquidity_intensity", "lower_bound": 0.3, "upper_bound": 1.0}
  ],
  "scoring_policy_digest": "<hex>"
}
```

Response:
```json
{
  "status": "complete",
  "found": true,
  "failure_id": "failure_<hash>",
  "scenario_hash": "<sha256>",
  "violated_property": { "id": "participation", "observed": 14.3, "threshold": 12 },
  "severity": { "score": 0.68, "components": {...} },
  "minimized": { "liquidity": 0.55, "latency_ms": 30, "volatility": 1.0 },
  "passing_neighbor": { "liquidity": 0.70, "latency_ms": 30, "volatility": 1.0 },
  "reproduction": { "seeds_tested": [41, 42], "seeds_failed": 2, "failure_rate": 1.0 },
  "evidence": {
    "scope": "adaptive_diagnostic",
    "mechanism": "adversarial_robustness_campaign_v1",
    "claim_boundary": "...",
    "limitations": ["Deterministic synthetic harness; not a live-trading claim."]
  },
  "replay_artifact_paths": {
    "orders": "artifacts/cmv-.../orders.parquet",
    "trades": "artifacts/cmv-.../trades.parquet",
    ...
  }
}
```

No field in the response may omit `limits` or `claim_boundary`.
