# UI Redesign Plan — Customer-Facing Quant UI (`app/static/break-test.html`)

## Goal
Redesign the existing Break Test page into a visually credible, hackathon-ready customer UI that exposes available backend capabilities without changing backend code. Only `app/static/break-test.html` will be modified.

## Backend Contract Summary
Relevant `/api/break-test/run` fields today:
- `closes`, `strategy_type`, `params`, `worlds_per_regime`, `fix_and_retest_params`, `forward_mode`, `strategy_code`
- `data_source`: `demo | yfinance | csv`
- `yfinance_tickers`, `yfinance_start`, `yfinance_end`, `lookback_period`
- `universe_preset`, `asset_count`
- `spread_bps`, `borrow_fee_bps`, `impact_beta`, `impact_mode`, `default_adv`

Relevant `/api/break-test/strategies` fields:
- `name`, `description`, `default_params`, `param_ranges`

Service-level support exists for `plain_english` compilation, but the API request model does not expose it yet. We will prepare the UI and service call path while emitting known-safe payload fields.

## Task-by-Task Plan

### 1) Data Source Selector
- Replace current 2-option data source with 3 options: Demo data (synthetic), Upload CSV, yfinance (free live).
- Show yfinance helper fields only when yfinance is chosen: ticker input, start/end dates, and “Load 20-year history” helper.
- Implement client-side loading using yfinance via backend data loader if available; otherwise prepare UI fields and mark helper as “connected when backend exposes /api/data/load.”

### 2) Plain-English Strategy Input
- Add a dedicated “Plain-English strategy” textarea and Compile button.
- Call `/api/break-test/strategies` on load and also on compile to surface template suggestions.
- When compile succeeds, prefill built-in dropdown and param fields from suggested template defaults.

### 3) Transaction Cost Model Inputs
- Add inputs: `spread_bps`, `borrow_fee_bps`, `impact_mode` (sqrt/linear), `default_adv`.
- Wire these into `/api/break-test/run` payload as top-level fields.

### 4) Universe Selector
- Add `asset_count` quick-select chips: 3, 8, 12.
- Add `universe_preset` dropdown: default, eight_assets, twelve_assets, custom_csv.
- Add file input for custom CSV when custom_csv is selected.
- Show dynamic labels with asset count and sector/ticker stubs based on chosen preset.

### 5) Execution Algorithm Selector
- Add selector: Signal-based (default), TWAP, POV, IS with parameter controls.
- Map these conceptually to `ExperimentSpec.strategy` and `participation_rate` once backend accepts them; in current UI, expose controls and include backend mapping note/mock wiring.

### 6) Correlation Matrix Heatmap
- Add a lightweight JS/CSS heatmap canvas from synthetic universe factor loadings or randomized correlation matrix.
- Render without external CDNs; use inline SVG/CSS grid.

### 7) Regime Timeline Visualization
- Render 4 regimes as color-coded blocks beneath the run panel.
- Use inline CSS timeline bars.

### 8) Corrected-vs-Baseline Comparison
- Render two equity curves side-by-side after correction/retest.
- Reuse existing SVG chart renderer and `renderComparison`.

### 9) Payload Wiring
- Ensure every new input is serialized into `/api/break-test/run` with correct field names from the API schema.

### 10) Responsive Layout
- Use existing responsive base and add mobile-first adjustments for sections, grids, and charts.

## Files Changed
- `app/static/break-test.html` only.

## Verification Steps
- Open `/break-test` and inspect DOM for all new controls.
- Confirm run payload includes new fields via browser network panel.
- Test responsive shrink to <=820px and <=520px.
- Compile plain-English text and observe template suggestions.
