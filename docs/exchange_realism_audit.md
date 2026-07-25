# Exchange & Matching Engine Realism Audit
## Findings ordered by severity for a Jane Street–grade execution simulator

**Status update (2026-07-21):** Hours 0–12 of `docs/12h_fix_plan.md` are marked **done**. Closed gaps include volume-time priority + queue telemetry, queue-conditioned fill probability, maker/taker toxicity direction, latency jitter module (`app/exchange/latency.py`), temporary-impact decay + locate/HTB supply curve, expanded TCA splits, failing `build_realism_report` harness, and `tests/test_exchange_realism.py`. Residual risks: V2 matching engine still primarily price-time FIFO (volume-time is on v1 `OrderBook`); iceberg display is best-effort; impact decay is not yet fully metaorder-aligned across repetitions.

### P0 — Structural correctness blockers
- **Simulated fees are not applied to execution outcomes.** `Exchange._settle()` computes maker/taker fees, but `ExchangeEngineV2.submit()` calls `super().submit()` without using the settled trades for strategy cost attribution beyond a single `fees_cents` bucket. There is no per-order, per-trade fee schedule split, so any algo-review or TCA path can be misattributing cost.
- **LOB fidelity is shallow; no hidden liquidity.** `OrderBook.snapshot()` only reports up to `book_depth_levels` displayed levels with no hidden/iceberg queue, no size-vs-display split, and no mid-price fallback beyond `last_price_ticks`. A realistic simulator needs iceberg queue state and hidden depth to study adverse selection and latency arbitrage.
- **Fill model lacks queue-position-dependent probability.** `OrderBook._match()` only apples a hard `max_match_quantity` volume cap; it never stochasticizes queue position into fill likelihood. Quant execution sims need fill probability conditioned on queue depth ahead rather than deterministic pecking.

### P1 — Fidelity gaps that change behavioral conclusions
- **No maker/taker adverse-selection split.** `OrderBook` records maker/taker roles, but `simulation.py` computes toxicity over signed taker flow only; there is no break-out of passive fill toxicity versus aggressive spread-capture toxicity. This makes it impossible to attribute P&L accurately or test maker-side quote failures.
- **Queue dynamics are FIFO-by-entry only, no volume-time priority scales.** `bid_levels`/`ask_levels` are deques keyed by `order_id`. There is no visible-time, priority-time, or multiplier-based queue scoring; no modeling of FIFO “stickiness” degradation under flow or imbalance.
- **Latency realism is coarse step-based, not message-lifecycle aware.** `_order_timing` assumes 0/2/20 ms profiles for feed/decision/entry/cancel, with arrival rounded to `MESSAGE_STEP_MS = 20`. No out-box latency, ACK latency, exchange processing time variance, or network jitter.
- **Market impact partial decomposition is flat.** `TransactionCostModel` uses Almgren-Chriss, but only applies at the trade level; there is no instantaneous vs decayed temporary impact model, no persistence curve, and no volume-dependence on the displayed depth in the simulator itself.

### P2 — Missing production TCA / borrow / locate mechanics
- **No short-sale locate failure distribution.** `TransactionCostModel._borrow_bps()` assumes locate + HTB rates are deterministic parameters. There is no stochastic locate-failure probability, aggregated inventory thresholding, or per-name supply curve.
- **No borrow-cost curve or HtB supply curve.** `htb_schedule` is a tiered lookup, not a curve against outstanding short interest, utilization, or days-to-cover. No inventory-driven short rebate modeling.
- **TCA is mostly implementation shortfall + toxicity heatmap.** `compute_tca_metrics()` provides arrival/VWAP/final-price slippage, and `tca_by_bucket()` buckets by participation rate. There is no slippage decomposition into timing, market, and operator cost; no sensitivity vs queue position; no child-order statistics.
- **No market disruption attribution.** `"market_disruption"` is approximated post-hoc as the max spread–shortfall delta; there is no regime-specific cost attribution, liquidity-withdrawal attribution, or microstructural event attribution.

### P3 — Calibration / verification
- **No executable test plan tied to acceptance thresholds.** `build_realism_report()` is purely descriptive; it does not fail a world sim on structural thresholds. No property-based tests for queue conservation, fill conservation, or replayability under stochastic latency.
