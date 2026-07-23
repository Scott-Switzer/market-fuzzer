# Synthetic Market Universe — Anti-Memorization & Anti-Gaming Design Notes

## 1. Procedural Generation Strategies That Defeat Memorization

### Current weak spots in `app/break_test/synthetic_market.py`
- `FACTOR_LOADINGS`, `FACTOR_CORRELATIONS`, and `FACTOR_ANNUAL_VOL` are static dicts/arrays. Across many runs, an AI observer can memorize exact systematic exposures.
- `_build_asset_factor_covariance(...)` applies a fixed rotation determined by the hardcoded factor matrix before Cholesky. The covariance structure is fully learnable from repeated observations.
- Asset fallback loadings for unknown `S0x` tickers are deterministic formulas of row index (`max(0.15, 1.0 - 0.07 * row_idx)`), so every extra synthetic asset has a precisely predictable loading profile.
- Regime sequences are seeded but the regime-switching mechanism itself is fixed: same Markov transitions, same duration distribution. Over many re-runs an AI can learn regime probabilities.

### Recommendations
1. **Latent factor rotation per session**  
   Introduce an orthogonal rotation of factor loadings so the principal components are not aligned to named factors (equity market, value, etc.). Implementation:
   - Add a `factor_rotation_seed: int | None` to `ResearchSyntheticMarketGenerator` or pass it into `_build_asset_factor_covariance`.
   - Draw `Q` via QR decomposition of `rng.standard_normal((k, k))` where `k = len(FACTOR_NAMES)`.
   - Transform loadings: `B_rot = B @ Q`.
   - Rotate factor vols/correlations jointly: `Sigma_rot = Q.T @ Sigma @ Q`.
   This preserves the eigenvalue spectrum but destroys the stable "global_equity_market" loading interpretation, defeating memorization of labeled factor betas.

2. **Seeded randomization of factor structure itself**  
   Instead of a single global correlation matrix, draw regime-conditional factor correlation matrices from a base matrix plus per-run noise bounded so the matrix stays PSD. Use the same seed family but with `seed + 7`, `seed + 13`, etc. for factor-level perturbation.

3. **Non-stationary asset parameters**  
   Extend `AssetFactorConfig` with tracked jitter that is deterministic per run but not identical across runs:
   - Macro beta: `beta_eff = clip(beta_base + rng.normal(0, beta_jitter), -3.0, 5.0)`
   - Idiosyncratic vol: `idio_eff = max(1e-6, idio_base + rng.normal(0, idio_jitter))`
   - Mean reversion: `mr_eff = clip(mr_base + rng.normal(0, mr_jitter), 0.0, 0.5)`
   Clamp changes small enough that calibration assertions are preserved but large enough that exact across-run memorization fails.

## 2. Freshness Mechanisms

### Current weak spots
- `asset_universe` lists in `SyntheticWorldCreate` are static built-in tuples.
- `_resolve_asset_universe` without a CSV/preset always returns the same `DEFAULT_ASSETS` plus incrementally generated `S03..` assets. No subsetting, rotation, or metadata variation.
- Global `WorldSpec` schema has no notion of session/release metadata beyond a free-text `tags` list.

### Recommendations
4. **Session-seeded subset pools**  
   Add a `session_seed: int = Field(default=0, ge=0)` to `SyntheticWorldCreate`. When `asset_count` is smaller than a master registry, draw a reproducible but session-unique subset:
   ```python
   rng = np.random.default_rng(session_seed)
   chosen = rng.choice(master_tickers, size=asset_count, replace=False)
   ```
   For identical `asset_count` and `universe_preset`, different `session_seed` values must produce different universes. Document this in the schema.

5. **Rotating universe pools**  
   Define families with non-overlapping synthetic sector exposures, e.g. `pool_a.csv`, `pool_b.csv`, `pool_c.csv`. Add a `universe_pool_id: str | None` field. The active pool is chosen deterministically from a rolling calendar key or `session_seed % len(pools)`. This limits AI modelers from seeing all sector combinations in a single year.

6. **Time-varying metadata**  
   Add `universe_snapshot_id` derived from `YYYY-QN` or `YYYY-MM` into `WorldSpec.universe_snapshot_id`. Rotate `company_name` templates and `sector` tokens quarterly so an old cached spec vector no longer names the same entity.

## 3. Anti-Overfitting Harness Design Strategies Used by Quant Firms

### Current weak spots
- `run_exchange_forward_test` uses fixed `forward_world_seed` and a cached `_WORLD_PROTOTYPE_CACHE`.
- `build_multi_asset_forward_results` is deterministic given inputs — same universe, same runs.
- `overfit_bounds.py` and `cross_val.py` exist, but they are post-hoc statistics, not harness failures. There is no adversarial validation, no live holdout sector.

### Recommendations
7. **Randomize universes per harness run**  
   In `run_exchange_forward_test`, if `universe_preset` points to an expanded pool larger than `asset_count`, randomize which tickers are selected per **run** using a seed derived from `hash(strategy_artifact_digest + regime_key + run_tag) & 0x7FFFFFFF`. Log the selected tickers in the result so auditors can reproduce but the strategy author cannot pre-fit.

8. **Held-out synthetic sectors**  
   Reserve certain sector archetypes for final OOS testing only. For example, out of 200 synthetic assets, mark 40 as `HOLDOUT_SECTOR`. Their loadings are published in aggregate only, not as individual rows, and they cannot be selected via `universe_preset` in training runs. Performance in held-out sectors becomes a pass/fail gate.

9. **Adversarial universe validation**  
   After strategy finalization, run a small adversarial pass where the factor rotation permutation is changed (`Q` from §1) and cross-sectional betas are permuted across assets. If the strategy’s Sharpe or fill-rate profile changes materially compared to the canonical run, flag as overfit. Capture a boolean `adversarial_robust` in `CompileResult`.

10. **Purge the prototype cache during randomization mode**  
    Add a flag `randomize_universe: bool = False` to `build_world`. When true, bypass `_WORLD_PROTOTYPE_CACHE` for that call or include the randomization tag in the cache key. Otherwise old cached universes leak into runs intended to be fresh.

## 4. Concrete Implementation Suggestions for the Repo Stack

### `AssetFactorConfig`
- Add new optional fields: `jitter_group: str | None = None`, `generation_epoch: str | None = None`.
- The existing `price_cache_factor_loading` is a good hook; extend it to control both correlation-to-cache and rotation weight so loadings are not memorized.

### `_resolve_asset_universe`
- Add parameters: `session_seed: int = 0`, `universe_pool_id: str | None = None`, `randomize: bool = False`.
- Maintain a separate `asset_universe_registry.csv` format for 200+ assets:
  ```
  ticker,sector,sector_group,base_macro_beta,base_idio_vol,base_mean_reversion,archetype_anchor
  SF01,Synthetic-FinTech,A,0.62,0.0015,0.024,SYNTH
  ...
  ```
- When `randomize=True`, sample `asset_count` rows without replacement via `rng.choice(registry, asset_count, replace=False)`.
- When `universe_preset` points to a pool, map it to `pool_id` rather than a hardcoded tuple.

### `generate_correlated_gbm_paths`
- Add argument `factor_rotation_seed: int | None = None` and `param_jitter_std: dict[str, float] | None`.
- Cache-clear the factor covariance matrix whenever rotation or jitter changes; do not reuse `_factor_covariance_cache`.
- Add emitted metadata:
  ```python
  payload["universe_meta"] = {
      "factor_rotation_seed": factor_rotation_seed,
      "param_jitter_applied": bool(param_jitter_std),
      "session_snapshot_id": generation_epoch,
  }
  ```

### `forward_world_seed` and harness
- Mix in a session/run hash:
  ```python
  def forward_world_seed(regime_index: int, world_idx: int, session_tag: str = "") -> int:
      return int(hashlib.sha256(f"{regime_index}:{world_idx}:{session_tag}".encode()).hexdigest()[:8], 16)
  ```
- Default `session_tag` to a UTC date string so daily runs produce a new universe family.

## 5. Integrating the 16-Company Fenrix Anonymized Bundle Without Leaking Real Identities

### Evidence from disk
- `/Users/scottthomasswitzer/Documents/scott-brain/22_Fenrix/anonymized_bundle/checksums.sha256` defines the canonical released artifacts.
- `qa/direct_identifier_scan.json`: **passed**; zero blocking/warning hits across 168 files, 1.5 MB.
- `qa/llm_blind_guess_COMPANY_004.json`: **passed**; strong refusal/uncertainty from the blind-guess model.
- `DATA_DICTIONARY.md`: transformation includes company-level scaling, metric-family perturbation, bounded stochastic noise, rounding. Exact transformation parameters are retained only in private QA artifacts.

### Recommendations
11. **Treat Fenrix as distributional template, never identifier source**  
    Import at generation time only:
    - `ratio_summary.csv` and bucket summaries → constrain simulated `initial_fundamental_value_ticks`, payout ratios, leverage bands.
    - `return_summary.md` and `price_series.csv` → calibrate idiosyncratic volatility and regime sensitivities.
    - `event_timeline.csv` → inform synthetic `EventSpec.narrative` tone and timing distributions.
    Do NOT propagate `company_name`, original sector strings, or synthetic SEC text directly into simulation-visible artifacts. Strip and re-synthesize narratives.

12. **Remap to synthetic archetype tickers**  
    Map each of the 16 companies to synthetic ticks `SF01..SF16` (or `FEN01..`). Derive archetype groups from Fenrix sector/industry clusters, but rename them generically: `Synthetic-Archetype-Group-A`, `Group-B2`, etc. Do not expose real industry keywords that enable search-engine identification.

13. **Checksum-gated ingestion adapter**  
    Build `app/fenrix_adapter.py` (or reuse `fenrix_adapter.py` shown in disk) that:
    - Validates SHA-256 of every ingested file against `checksums.sha256`.
    - Rejects any file missing from the registry or exceeding expected size.
    - Emits a release-gate manifest that records the exact adapter version and checksum hash.

14. **QA-gated release step**  
    Before any generated universe is marked public:
    - Re-run direct-identifier scan against generated `AssetSpec` fields.
    - Run an LLM blind-guess probe on the generated narrative/rich-text fields; require `"verdict": "PASS"` equivalent.
    - Fail the release if real keywords like actual company/service names appear in any exported manifest, spec, or brief.

15. **Private-seed separation**  
    Keep Fenrix transformations and exact private QA artifacts out of the public repo. Release only:
    - `fenrix_archetype_parameter_table.csv`
    - `fenrix_release_manifest.json`
    - Adapter code with hardcoded public checksums

This preserves scientific/calibration utility of Fenrix while ensuring the sealed auditor cannot be reverse-engineered into real-company exposure.
