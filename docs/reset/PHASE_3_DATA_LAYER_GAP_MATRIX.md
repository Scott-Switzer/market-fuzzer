# Phase 3 — Data Layer Gap Matrix

Branch: `reset/fenrix-data-layer-v1`  
Base: `c088b0fa95da3feee891d33c9f6599f80b5d0f9d` (merge of PR #47)  
Purpose: identify every data-path defect against the Phase 3 target architecture before implementation.

---

## 1. Current data paths (audit)

| Path | Entry | Output | Issues |
|---|---|---|---|
| **Canonical V2 backtest** | `POST /v2/backtests` → `run_backtest()` → `acquire_panel()` | `MarketDataPanel` (frozen dataclass) | No provider registry; `if/elif` chain on source string; no `MarketDataRequest` contract; no digest persisted per-run; no frozen panel reload for campaign baseline |
| **Canonical V2 campaign** | `POST /v2/campaigns` → `run_campaign()` → `acquire_panel()` or `panel_from_dict()` | Same panel or reconstructed from artifact | Baseline panel reload works but does not verify dataset digest; no eligibility mask; no `as_of` enforcement |
| **Demo fixture** | `build_demo_panel()` | Synthetic T×N panel | Weekday-only calendar not declared as synthetic; no adjustment policy; no missing-data policy; no provider version |
| **yfinance** | `build_yfinance_panel()` → `acquire()` | Real T×N panel | No adjustment policy declaration; no provider capability declaration; no `as_of` timestamp; cache path hard-coded; no frozen artifact manifest |
| **Legacy submission path** | `submission/orchestrator.py` → `submission/engine.py` | `MarketDataPanel` (same class) | Different provenance model; no canonical digest; no frozen artifacts; different calendar handling |
| **Domain data source** | `app/domain/data_source.py` | `DataProvenance` (Pydantic) | Duplicated with `panels.DataProvenance` (dataclass); no provider registry; no capability matrix |

---

## 2. Defects against Phase 3 target

### D1 — No canonical `MarketDataRequest` contract
**Current:** `acquire_panel(source, universe, benchmark, ...)` with raw strings.  
**Required:** Typed, immutable `MarketDataRequest` with: instruments, date range, frequency, fields, calendar policy, adjustment policy, missing-data policy, benchmark request, eligibility source, `as_of` timestamp, provider name, provider config version, `allow_synthetic_fixture` flag.  
**Files:** `app/strategy_lab/canonical/data_service.py` (replace with `app/market_data/`).  
**Tests:** Request validation, 422 on invalid, `as_of` enforcement.

### D2 — No provider registry protocol
**Current:** `if source == "demo_fixture" ... elif source == "yfinance" ...`  
**Required:** `MarketDataProvider` protocol with declared capabilities (frequencies, asset types, fields, adjustment policies, point-in-time safe, delisted support, volume, benchmark). Registry rejects unsupported requests.  
**Files:** New `app/market_data/registry.py`, `app/market_data/providers/`.  
**Tests:** Unknown provider 422, unsupported capability 422, registry lookup.

### D3 — `MarketDataPanel` lacks Phase 3 fields
**Current:** `dates, assets, open, high, low, close, volume, benchmark_close, metadata, provenance`  
**Missing:** `eligibility_mask`, `observed_mask`, `imputation_mask`, `as_of`, `dataset_digest`, `calendar_policy`, `adjustment_policy`, `missing_data_policy`, `provider`, `provider_version`, `retrieval_timestamp`.  
**Required:** Add fields; validate masks align; validate no imputation marked as observation; validate no eligibility before inception.  
**Files:** `app/market_data/panel.py` (new canonical), `app/strategy_lab/submission/panels.py` (legacy, adapt or deprecate).  
**Tests:** Mask alignment, inception validation, benchmark isolation, digest determinism.

### D4 — No canonical dataset digest
**Current:** `_digest_panel()` covers OHLCV + benchmark + metadata + source but not masks, policies, `as_of`, or provider version.  
**Required:** SHA-256 over: schema version, timestamps, instrument IDs, OHLCV, benchmark, eligibility mask, observed mask, imputation mask, adjustment policy, calendar policy, missing-data policy, provider identity + version, `as_of`, source metadata affecting interpretation.  
**Tests:** Identical panel → identical digest; changed price → different digest; changed policy → different digest; reordered assets → same digest; irrelevant metadata → unchanged digest.

### D5 — No frozen panel artifact manifest
**Current:** Panels serialized per-run to `runs/{id}/input-panel.json` but no manifest, no digest verification on reload, no dataset table.  
**Required:** `datasets` table with `dataset_id, project_id, canonical_digest, provider, provider_version, request_json, provenance_json, quality_json, artifact_manifest_ref, created_at`. Runs reference `dataset_digest`. Campaign baseline verifies digest.  
**Files:** `app/persistence/models.py` (+`DatasetRow`), migration, `app/market_data/artifacts.py`.  
**Tests:** Frozen panel reload after restart, manifest verification, tamper detection, digest match.

### D6 — No point-in-time eligibility source
**Current:** `AssetMetadata.point_in_time` boolean flag; no eligibility mask; no `as_of` enforcement.  
**Required:** `EligibilitySource` enum: `STATIC_DECLARED_UNIVERSE`, `PROVIDER_OBSERVED_AVAILABILITY`, `POINT_IN_TIME_MEMBERSHIP`, `SYNTHETIC_FIXTURE`. Current-membership cannot be labeled point-in-time. `as_of` must be enforced.  
**Files:** `app/market_data/eligibility.py`, `app/market_data/panel.py`.  
**Tests:** Current membership → survivorship warning; missing universe → resolution required; no eligibility before first observation; `as_of` violation → 422.

### D7 — No explicit calendar policy
**Current:** Weekday-only synthetic calendar not labeled; yfinance calendar assumed NYSE.  
**Required:** `CalendarPolicy` enum: `PROVIDER_OBSERVED`, `NAMED_EXCHANGE`, `SYNTHETIC_WEEKDAY`. Synthetic fixture must not claim exchange realism.  
**Files:** `app/market_data/calendar.py`, `app/market_data/panel.py`.  
**Tests:** Calendar policy recorded, synthetic labeled, unknown calendar 422.

### D8 — No explicit adjustment policy
**Current:** yfinance uses `auto_adjust=False` but no policy declaration; no support for split/dividend/total-return distinction.  
**Required:** `AdjustmentPolicy` enum: `RAW`, `SPLIT_ADJUSTED`, `SPLIT_AND_DIVIDEND_ADJUSTED`, `TOTAL_RETURN`. yfinance adapter must declare and record.  
**Files:** `app/market_data/adjustments.py`, `app/market_data/providers/yfinance_adapter.py`.  
**Tests:** Adjustment policy recorded, unsupported policy 422, changed policy changes digest.

### D9 — No explicit missing-data policy
**Current:** No policy; yfinance fills NaN with zeros implicitly; no forward-fill limits.  
**Required:** `MissingDataPolicy` enum: `REJECT`, `PRESERVE_MISSING`, `LIMITED_FORWARD_FILL` (max gap, field restrictions). Never fill volume, never fill before inception, never fill across ineligible periods.  
**Files:** `app/market_data/quality.py`, `app/market_data/panel.py`.  
**Tests:** Reject on required gap, preserve keeps mask, limited fill respects max gap, volume never filled.

### D10 — No data quality report
**Current:** Coverage dict in provenance only.  
**Required:** Structured `DataQualityReport`: requested/returned/missing instruments, first/last valid per instrument, observed/missing/imputed bar counts, longest missing streak, invalid price count, invalid OHLC count, zero-volume count, stale streaks, eligibility coverage, benchmark coverage, history-requirement result, warnings vs fatal errors.  
**Files:** `app/market_data/quality.py`, `app/market_data/panel.py`.  
**Tests:** Fatal vs warning distinction, quality report persisted, insufficient history → 422.

### D11 — No provider capability declaration
**Current:** yfinance adapter has no declared capabilities; no version pinning.  
**Required:** `ProviderCapabilities` with: supported frequencies, asset types, fields, adjustment policies, timestamp semantics, timezone behavior, point-in-time safe, revision risk, delisted support, volume, benchmark.  
**Files:** `app/market_data/providers/protocol.py`, `app/market_data/providers/yfinance_adapter.py`.  
**Tests:** Unsupported capability rejected, capability matrix documented.

### D12 — Synthetic fixture not properly isolated
**Current:** `build_demo_panel()` creates synthetic data but no `allow_synthetic_fixture` flag check; no explicit synthetic provider registration.  
**Required:** Synthetic fixture as registered provider with `SYNTHETIC_FIXTURE` eligibility source; requires explicit opt-in; weekday-only calendar labeled synthetic; stable seed; full OHLCV; provenance states generator version + limitations.  
**Files:** `app/market_data/providers/synthetic_fixture.py`.  
**Tests:** Synthetic requires opt-in, deterministic generation, labeled limitations.

### D13 — No `as_of` timestamp enforcement
**Current:** No `as_of` concept; data always "latest available".  
**Required:** `as_of` = latest information timestamp allowed. Request with `as_of` in past must not return data after that timestamp.  
**Files:** `app/market_data/contracts.py`, `app/market_data/panel.py`.  
**Tests:** `as_of` violation rejected, historical `as_of` respected.

### D14 — Duplicate data contracts
**Current:** `panels.DataProvenance` (dataclass) vs `domain.data_source.DataProvenance` (Pydantic); `panels.MarketDataPanel` vs `historical.data_contracts.HistoricalDataContract`.  
**Required:** Single canonical `MarketDataPanel` + `MarketDataRequest` + `DataProvenance` in `app/market_data/`. Legacy paths adapted or quarantined.  
**Files:** `app/market_data/` (new), `app/strategy_lab/submission/panels.py` (deprecate), `app/domain/data_source.py` (deprecate or adapt).  
**Tests:** No duplicate active contracts; honesty test enforces canonical path only.

### D15 — No five-family history requirement enforcement
**Current:** `check_required_history()` validates contiguous bars but not executor-specific requirements.  
**Required:** `HistoryRequirements` contract from Phase 2.6 enforced per executor: cross-sectional factor (12-1), long-only ranking (declared lookback), SMA (slow window), static allocation (sufficient execution bars), tactical allocation (ranking + trend). Structured error with required vs available.  
**Files:** `app/market_data/quality.py`, `app/strategy_lab/canonical/backtest_service.py`.  
**Tests:** Five-family table, insufficient history → 422 with structured payload.

### D16 — Campaign baseline does not verify dataset digest
**Current:** Campaign loads `input-panel.json` but does not verify the stored digest matches the panel.  
**Required:** Campaign baseline reload verifies `dataset_digest` against stored manifest; mismatch → 422.  
**Files:** `app/strategy_lab/canonical/campaign_service.py`, `app/market_data/artifacts.py`.  
**Tests:** Tampered baseline → 422, matching digest → success.

### D17 — No V2 response dataset metadata
**Current:** `BacktestResponse` has `data_provenance` but no `dataset_digest`, `provider`, `calendar_policy`, `adjustment_policy`.  
**Required:** Response includes dataset digest, provider, calendar, adjustment, quality summary, artifact references.  
**Files:** `app/strategy_lab/canonical/contracts.py`, `app/strategy_lab/canonical/backtest_service.py`.  
**Tests:** Response fields present, digest matches stored.

### D18 — No audit endpoint dataset verification
**Current:** Audit verifies strategy hash + artifact hashes but not dataset digest.  
**Required:** Audit includes and verifies dataset digest; mismatch → integrity error.  
**Files:** `app/strategy_lab/canonical/evidence_service.py`.  
**Tests:** Audit digest verification, tampered dataset → integrity error.

---

## 3. Duplicated contracts to resolve

| Contract | Location 1 | Location 2 | Action |
|---|---|---|---|
| `DataProvenance` | `panels.py` (dataclass) | `domain/data_source.py` (Pydantic) | Keep canonical in `app/market_data/contracts.py`; deprecate others |
| `MarketDataPanel` | `panels.py` (dataclass) | `historical/data_contracts.py` | Keep canonical in `app/market_data/panel.py`; deprecate others |
| `DataSourceProvenance` | `canonical/contracts.py` (Pydantic) | `panels.py` (dataclass) | Merge into canonical `MarketDataProvenance` |
| `DataTier` | `domain/data_source.py` | `panels.py` (tier int) | Replace with `ProviderCapabilities` |
| Calendar handling | `data_service.py` `_business_days()` | `panels.py` (implicit) | Explicit `CalendarPolicy` in canonical |
| yfinance acquisition | `submission/yfinance_adapter.py` | `canonical/data_service.py` | Single canonical `YFinanceProvider` in `app/market_data/providers/` |

---

## 4. Silent defaults and synthetic fallback risks

| Risk | Location | Mitigation |
|---|---|---|
| Demo fixture used without explicit opt-in | `acquire_panel(source="demo_fixture")` | `allow_synthetic_fixture` flag in request; default `False` |
| Weekday calendar not labeled synthetic | `_business_days()` | `CalendarPolicy.SYNTHETIC_WEEKDAY` |
| yfinance cache silently returns stale data | `yfinance_adapter.py` cache | Cache key includes `as_of`; stale cache rejected |
| Forward-fill without policy | yfinance NaN handling | `MissingDataPolicy` enforced; volume never filled |
| Current membership labeled point-in-time | `AssetMetadata.point_in_time` | `EligibilitySource` enum; current membership → warning |
| Benchmark added to tradable without flag | `build_yfinance_panel()` SPY handling | `benchmark_tradable` flag enforced; non-tradable benchmark separate |

---

## 5. Provider-specific leakage

| Leak | Location | Fix |
|---|---|---|
| yfinance `auto_adjust` behavior | `yfinance_adapter.py` | Explicit `AdjustmentPolicy` declaration |
| yfinance multi-index column format | `yfinance_adapter.py` | Normalize to canonical panel immediately |
| SPY hard-coded as benchmark | `build_yfinance_panel()` | Benchmark from request, not hard-coded |
| yfinance `threads=False` assumption | `yfinance_adapter.py` | Provider capability declaration |
| yfinance `datetime.utcnow()` deprecation | `yfinance_adapter.py` | Use `datetime.now(UTC)` |
| Fenrix relative dates | `fenrix_adapter.py` | `dates_are_relative` flag; not point-in-time |

---

## 6. Provenance gaps

| Gap | Current | Required |
|---|---|---|
| Provider version | Not recorded | `provider_version` in manifest |
| Retrieval timestamp | `datetime.utcnow()` (deprecated) | `datetime.now(UTC)` in request + panel |
| `as_of` timestamp | Not present | Required in request; enforced in panel |
| Adjustment policy | Implicit | Explicit enum + recorded |
| Calendar policy | Implicit | Explicit enum + recorded |
| Missing-data policy | None | Explicit enum + recorded |
| Eligibility source | `point_in_time` bool | `EligibilitySource` enum |
| Dataset digest | Not persisted | SHA-256 canonical digest persisted + verified |
| Generator version (synthetic) | Not recorded | `synthetic-gen/v1.0` in provenance |

---

## 7. Calendar and adjustment ambiguity

| Ambiguity | Current | Required |
|---|---|---|
| Weekday vs exchange calendar | Weekday-only not labeled | `CalendarPolicy.SYNTHETIC_WEEKDAY` |
| yfinance calendar | Assumed NYSE | `CalendarPolicy.PROVIDER_OBSERVED` or `NAMED_EXCHANGE` |
| yfinance adjustment | `auto_adjust=False` but no policy | `AdjustmentPolicy.RAW` or `SPLIT_ADJUSTED` |
| Total return support | Not supported | `AdjustmentPolicy.TOTAL_RETURN` (only if provider supports) |
| Split adjustment | Not distinguished | `SPLIT_ADJUSTED` vs `SPLIT_AND_DIVIDEND_ADJUSTED` |

---

## 8. Point-in-time limitations

| Limitation | Current | Required |
|---|---|---|
| No `as_of` enforcement | Always latest | `as_of` timestamp enforced |
| No eligibility mask | Boolean flag only | Per-bar eligibility mask |
| No survivorship handling | Current membership assumed | `EligibilitySource` + warning |
| No delisted support | Not declared | Provider capability |
| No inception date tracking | Not tracked | First valid observation per instrument |

---

## 9. Migration requirements

| Change | Type | Notes |
|---|---|---|
| `datasets` table | Additive | `dataset_id, project_id, canonical_digest, provider, provider_version, request_json, provenance_json, quality_json, artifact_manifest_ref, created_at` |
| `runs.dataset_digest` | Additive | Link run to dataset |
| `runs.data_mode` | Keep | Backward compatible |
| `campaigns.base_panel_digest` | Keep | Verify against dataset digest |
| Index on `datasets.canonical_digest` | Additive | For deduplication |
| Index on `datasets.project_id` | Additive | For project isolation |

**Identity semantics:** `canonical_digest` is NOT globally unique (two projects may retain same data). Uniqueness on `(project_id, canonical_digest)`.

---

## 10. Tests required (before/after)

| Test | Current | Required |
|---|---|---|
| Panel validation | Basic shape/finite | + masks, eligibility, inception, benchmark isolation |
| Digest determinism | Not tested | Identical → same digest; changed → different digest |
| Provider registry | Not tested | Unknown provider 422, unsupported capability 422 |
| Synthetic opt-in | Not tested | Requires explicit flag |
| Point-in-time | Not tested | `as_of` enforcement, eligibility mask, survivorship warning |
| Missing data | Not tested | Reject/preserve/fill policies, volume never filled |
| History requirements | Partial | Five-family table, structured 422 |
| Frozen panel reload | Partial | Digest verification, tamper detection |
| Campaign baseline | Partial | Digest match, no reacquisition |
| Five-family vertical | Partial | Full end-to-end per family |
| Metamorphic | Not tested | Asset order, benchmark addition, display metadata |
| PostgreSQL | Partial | Migration, constraints, dataset table |

---

## 11. Completion evidence

| Gate | Evidence |
|---|---|
| Every V2 backtest uses canonical `MarketDataPanel` | `backtest_service.py` imports from `app/market_data/` |
| No provider-specific dataframe crosses into execution | `run_strategy()` receives canonical panel only |
| No silent synthetic fallback | `allow_synthetic_fixture` flag; honesty test |
| Every run stores + verifies dataset digest | `runs.dataset_digest` + artifact manifest |
| Every run can reload exact frozen panel | `panel_from_dict()` + digest verify |
| Campaign baseline never reacquires | Digest match + frozen panel reuse |
| Adjustment/calendar policies explicit | `AdjustmentPolicy` + `CalendarPolicy` enums |
| Current-membership never mislabeled | `EligibilitySource` + survivorship warning |
| Executor history requirements enforced | `HistoryRequirements` contract + structured 422 |
| Benchmark isolation intact | Non-tradable benchmark separate from assets |
| Data quality failures structured | `DataQualityReport` with fatal vs warning |
| Five families pass vertical slices | End-to-end test per family |
| PostgreSQL migration passes | `alembic upgrade head` + `alembic check` |
| Exact-head CI green | GitHub Actions run IDs |
| No prior test weakened | All existing tests pass |

---

## 12. Implementation sequence

1. **Phase 3.0** — this gap matrix + audit document
2. **Phase 3.1** — canonical contracts: `panel.py`, `contracts.py`, `calendar.py`, `adjustments.py`, `quality.py`, `digest.py`, `errors.py`
3. **Phase 3.2** — provider protocol + registry + synthetic fixture + yfinance
4. **Phase 3.3** — frozen artifacts + `datasets` table + migration
5. **Phase 3.4** — V2 backtest/campaign cutover + legacy quarantine
6. **Phase 3.5** — adversarial tests + verification gates

---

*This document is the plan of record. Each defect row is closed by a commit. The gap matrix must be updated if new defects are discovered during implementation.*
