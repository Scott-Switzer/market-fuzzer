# Synthetic World-Bank Architecture — MVP

## 1. Purpose and Scope

The World Bank turns every synthetic generator, calibration pack, similarity report,
and leakage result into an inspectable, signed artifact. This document defines:

- MVP generator families
- World manifest schema
- Calibration/evaluation separation
- Asset-anonymization controls and leakage tests
- Correlation/factor stress plan
- Future scalable Bank architecture

## 2. Generator Families

### 2.1 `MarketFactorGenerator` (primary)
Source of truth for all price-path generation. Implementation: `app/break_test/synthetic_market.py`.

| family_id | description |
|-----------|-------------|
| `market_factor_v1` | Regime-switching factor model with GJR-GARCH, stochastic jump intensity, and factor-Cholesky covariance. |

Supported modes:
- `generate_path` — single-asset regime-conditional path
- `generate_correlated_gbm_paths` — multi-asset factor-covariance paths
- `generate_regime_switching_path` — 3-state Markov path
- `generate_regime_switching_correlated_paths` — DCC-style correlated regime-switching path

### 2.2 `UniverseGenerator` (structural)
Source of truth for asset-registry generation. Implementation: `app/break_test/universe_anti_memo.py`.

| family_id | description |
|-----------|-------------|
| `procedural_universe_v1` | Anti-memoized session-seeded universe with sector weights, adversarial selection, time-varying refresh, and heldout forward sectors. |

### 2.3 `PresetUniverseGenerator` (bounded real proxies)
Source of truth for bounded real-ticker presets. Implementation: `app/break_test/daily_macro_100_preset.py`.

| family_id | description |
|-----------|-------------|
| `daily_macro_100_preset_v1` | 100-ticker macro-focused preset with yfinance download and artifact fallback. |

### 2.4 `PointProcessGenerator` and `LatentFactorGenerator` (event/experimental)
Re-exported from `app/generators/v1.py` for declared experiments.

| family_id | description |
|-----------|-------------|
| `regime_switching_point_process_v1` | Quiet/stressed/recovery regime schedules with side persistence. |
| `correlated_latent_factor_v1` | Shared latent factor with structural break schedule. |

### 2.5 File Targets

```
app/break_test/
  synthetic_market.py           # MarketFactorGenerator source of truth
  universe_anti_memo.py         # UniverseGenerator source of truth
  daily_macro_100_preset.py     # Preset catalog + loader
  regimes.py                    # Regime debug helpers, forward test runner

app/generators/
  v1.py                         # PointProcess + LatentFactor experimental families
  bank_adapter.py               # NEW: adapter that wins all generators through
                                # the Bank estimator interface

app/world/
  bank.py                       # NEW: SyntheticWorldBank — immutable bank
  generator_family_registry.py  # NEW: family_id → concrete implementation binding
  manifest_schema.py            # NEW: WorldManifest, AssetManifest, CalibrationRef

app/bank/
  __init__.py                   # NEW
  world_bank.py                 # NEW: high-level bank façade for sessions
  manifest.py                   # NEW: manifest write/read/sign/verify
  seed_policy.py                # NEW: ENTROPY / FROZEN / DISCRETE_DIFFICULTY
  anonymization.py             # NEW: sector anonymization, heldout controls,
                                # leakage tests
  stress_plan.py               # NEW: Correlation and factor stress scenarios
  diagnostics.py               # NEW: stylized-fact checks, benchmark comparison

tests/bank/
  test_world_manifest.py
  test_seed_policy.py
  test_anonymization.py
  test_stress_plan.py
  test_cross_family_leakage.py
```

## 3. World Manifest Schema

Every Bank admission produces a signed manifest artifact. Key types are declared
in `app/world/manifest_schema.py`.

### 3.1 `WorldManifest`

```python
class WorldManifest(StrictModel):
    manifest_id: str
    schema_version: Literal["1.0"] = "1.0"
    family_id: str
    generator_version: str
    seed: int
    entropy_source: Literal["ENTROPY", "FROZEN", "DISCRETE_DIFFICULTY"]
    created_at: str  # UTC ISO-8601
    generator_assumptions: tuple[str, ...]
    limitations: tuple[str, ...]
    supported_claims: tuple[str, ...]
    prohibited_claims: tuple[str, ...]
    calibration_pack_id: str | None = None
    asset_manifest: AssetManifest
    parameter_overrides: dict[str, float | int] | None = None
    seed_manifest: SeedManifest
    stylized_fact_diagnostics: dict[str, float | int]
    correlation_stress_applied: CorrelationStressManifest | None = None
    anonymization_manifest: AnonymizationManifest | None = None
    leakage_tests: LeakageManifest | None = None
    digest: str  # sha256 over canonical JSON
    signature_alg: Literal["sha256"] = "sha256"
```

### 3.2 `AssetManifest`

```python
class AssetManifest(StrictModel):
    asset_count: int
    real_ticker_count: int = 0
    synthetic_asset_count: int
    anonymized_count: int = 0
    strategy_asset_ticker: str | None = None
    sector_manifest: list[SectorManifestEntry] = Field(default_factory=list)
    corporate_actions: dict[str, dict[str, object]] | None = None
    delisted_assets: tuple[str, ...] = ()
```

### 3.3 `SeedManifest`

```python
class SeedManifest(StrictModel):
    policy: Literal["ENTROPY", "FROZEN", "DISCRETE_DIFFICULTY"]
    user_supplied_seed: int | None = None
    derived_session_hash: str | None = None
    universe_seed_hash: str | None = None
    challenge_seed: str | None = None
    commit_hash: str | None = None
```

### 3.4 `CorrelationStressManifest`

```python
class CorrelationStressManifest(StrictModel):
    scenario: Literal["base", "neutral", "flight_to_quality", "dollar_crunch",
                      "commodity_surge", "crypto_contagion"]
    applied_at: str
    multiplier: float
    offdiagonal_scale_bps: float | None = None
    factor_rotations: dict[str, tuple[float, float]] | None = None
```

### 3.5 `AnonymizationManifest`

```python
class AnonymizationManifest(StrictModel):
    mode: Literal["PROMPT_SAFE", "FULL_DECODED", "HELDOUT_HIDDEN"]
    applied: bool
    heldout_sector_forward_count: int = 0
    prompt_decoded_tickers: tuple[str, ...] = ()
    decoded_names: dict[str, str] | None = None
    decoded_sector_map: dict[str, str] | None = None
    limits_applied: tuple[str, ...] = ()
```

### 3.6 `LeakageManifest`

```python
class LeakageManifest(StrictModel):
    world_version: str
    reference_checksums: dict[str, str]
    exact_duplicate_trajectories: bool = False
    nearest_window_correlation: float | None = None
    nearest_window_normalized_rmse: float | None = None
    similarity_warning: bool
    empirical_bootstrap_train_test_distance: float | None = None
    empirical_bootstrap_acceptance_threshold: float
    passed: bool
```

## 4. Calibration / Evaluation Separation

### 4.1 Principle

Calibration shapes generator parameters.
Evaluation measures generator-backed diagnostics on **new seeds**.

Calibration artifacts (`CalibrationPackV1`, `BootstrapCalibrationResult`) live in
`app/calibration/models.py` and must never contain raw price rows.

Evaluation artifacts live in the bank manifest as
`stylized_fact_diagnostics` and `validation_quality_score`.

### 4.2 File Targets

```
app/calibration/
  models.py               # source of truth for pack/interval/stability/pack schema
  bootstrap.py            # parametric bootstrap with accepted/rejected candidates
  compiler.py             # compile local OHLCV proxies into pack aggregates
  local_market_data.py    # bounded adapter from local OHLCV to CalibrationPackV1
  exchange_hooks.py       # map pack aggregates → ExchangeSpec
  similarity.py           # trajectory similarity checks only

app/evaluation/
  evidence_v1.py          # decision evidence
  sealed_campaign_*.py    # sealed evaluation jobs

app/break_test/
  validation_quality.py   # scalar quality score across gates

tests/test_calibration*.py
tests/test_validation_quality.py
```

### 4.3 Registration Flow

1. Build `calibration_pack_id` from `CompileResult.spec_hash`.
2. Store accepted `parameter_set_id` in `WorldSpec.calibration_parameter_set_id`.
3. Evaluation engine requires stored parameter set ID, never raw pack rows.

## 5. Asset Anonymization Controls and Leakage Tests

### 5.1 Anonymization Modes

| mode | guarantee |
|------|-----------|
| `PROMPT_SAFE` | All prompt-exposed ids are synthetic. Real tickers are never surfaced. |
| `FULL_DECODED` | All decoded mappings are disclosed. |
| `HELDOUT_HIDDEN` | Decoded mappings exist but are suppressed from public reports. |

Default for evaluation: `HELDOUT_HIDDEN`.

### 5.2 Controls

- `FROZEN` seeds permit replay only; `ENTROPY`/`DISCRETE_DIFFICULTY` require commit hash.
- Heldout sectors (`Crypto`, `Commodities/Metals`) can be forward-injected only through
  `include_heldout_forward` with explicit `heldout_forward_count`.
- `bootstrap_distance` threshold enforced between train/validation/test
  aggregations and candidate parameter sets.
- Reference checksums stored in manifest; reference rows never persisted.

### 5.3 Leakage Tests

`app/world/anonymization.py` exposes:

- `trajectory_similarity(world, reference_prices)` — exact duplicate check and
  rolling window correlation across normalized log-returns.
- `reference_checksum(reference_prices)` — never stores raw rows.
- `train_test_hard_split(pack)` — asserts date exclusivity across windows.
- `empirical_bootstrap_distance(candidate, pack, window)` — accepted if distance
  only exceeds threshold in held-out window.

## 6. Correlation and Factor Stress Plan

Stress scenarios are applied at world admission time and recorded in
`CorrelationStressManifest`.

### 6.1 Scenarios

| scenario | target regimes | stress rule |
|----------|----------------|-------------|
| `base` | none | normal factor correlations |
| `neutral` | all | small rotation angle between equity and rates |
| `flight_to_quality` | high_vol / crisis | increase rates-equity negative correlation by 0.20; decrease commodity correlation with equities |
| `dollar_crunch` | crisis | increase FX-equity correlation; decrease credit correlation with rates |
| `commodity_surge` | stress | increase commodity-equity and rates-credit correlation |
| `crypto_contagion` | sudden_selloff | increase crypto-equity correlation by 0.35 |

### 6.2 Implementation

File: `app/world/stress_plan.py`

- `choose_base_regime(length, rng)` selects most common regime in path for base correlation.
- `apply_stress(base_corr, scenario, rng)` returns rotated correlation matrix.
- `build_stress_covariance(asset_tickers, annual_factor_vols, stressed_corr)` returns
  regime-scaled covariance.

## 7. Future Scalable Bank Architecture

### 7.1 Requirements

1. Streaming admission of new generator families without schema changes.
2. Cross-family determinism via content-addressed generation graphs.
3. Observer pattern for external plugins (verifiers, exporters, dashboards).
4. Sharded artifact storage with locality-hashed manifest directories.
5. Parallel regression discovery blocks.

### 7.2 Recommended Components

```
app/bank/
  world_bank.py             # façade: admit, eval, replay, export, snapshot
  manifest.py               # read/write/sign/verify immutable manifests
  seed_policy.py            # ENTROPY / FROZEN / DISCRETE_DIFFICULTY
  anonymization.py          # sector anonymization + heldout controls + leakage
  stress_plan.py            # correlation/factor stress scenarios
  diagnostics.py            # stylized-fact checks + benchmark comparison
  plugin_interface.py       # BankPlugin protocol + observer hooks
  retention.py              # signed snapshot packaging
  locality.py               # sha256(first-2)-sharded manifest directories
  family_index.py           # content-addressed family registry
```

### 7.3 Plugin Interface

```python
class BankPlugin(Protocol):
    def on_admit(self, manifest: WorldManifest) -> None: ...
    def on_evaluate(self, manifest: WorldManifest, score: float) -> None: ...
    def on_replay(self, manifest: WorldManifest, replayed_digest: str) -> None: ...
    def on_export(self, manifest: WorldManifest, target: str) -> None: ...
```

## 8. Registration Contract

Every generator family must produce a `GeneratedWorldV1` or equivalent payload
and a `WorldManifest`. The bank rejects any admission lacking:

1. `digest`
2. `seed_manifest` with valid `policy`
3. `supported_claims` and `prohibited_claims`
4. `stylized_fact_diagnostics`
5. `leakage_tests.passed == True`

Any generator without a registered `family_id` is refused at admission.

## 9. Next Steps

1. Add `app/world/`, `app/bank/`, and tests under `tests/bank/`.
2. Refactor `ResearchSyntheticMarketGenerator` into a `MarketFactorGenerator`
   that emits a `WorldManifest` alongside each path payload.
3. Add `WorldManifest.persistence.write(target_dir)` and `WorldBank.admit(manifest)`.
4. Wire `seed_policy` validation into break-test service entry points.
5. Add `COSIGN_REPLAY=1` environment toggle for dual-output verification jobs.
