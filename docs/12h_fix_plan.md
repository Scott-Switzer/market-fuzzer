# 12-Hour Exchange/Matching Engine Realism Fix Plan

**Status: done** (implemented 2026-07-21)

Target baseline: `main`-branch exchange can pass the drafted `test_exchange_realism.py` properties and support the existing sealed-evaluation contract.

## Hour 0–1 — Scaffold / measurements
Targets: `app/exchange/order_book.py`, `tests/`
- Add queue telemetry fields to `OrderBook`: `queue_ahead_at_price`, `total_queue_ahead`, `disclosed_size`, `hidden_size`.
- Add `test_exchange_realism.py` skeleton with deterministic invariants for queue conservation, fill conservation, and FIFO ordering.

## Hour 1–3 — Queue dynamics + volume-time priority
Targets: `app/exchange/order_book.py`, `app/exchange/v2_matching.py`, `app/exchange/orders.py`
- Replace flat `deque[str]` queues with `deque[OrderRef]` storing `(order_id, visible_qty, hidden_qty, priority_score)`.
- Volume-time priority formula: `priority_score = visible_qty * time_weight`, where `time_weight` decays with `order_age_in_steps`.
- Add `peek(n)` and `drain_by_priority()` helpers; update matching to walk priority order.
- Add iceberg-limit order support: `OrderTimeInForce.GTC` + `display_quantity`; refill cured hidden qty from parent size after fills.

## Hour 3–5 — Fill model + maker/taker adverse-selection split
Targets: `app/exchange/order_book.py`, `app/exchange/orders.py`
- Add `_fill_probability(incoming, maker, depth_ahead, time_in_queue)` with noise calibrated to empirical distribution.
- Record `trade_toxicity_direction` in `Trade`: positive when taker hits passive side.
- Update `app/simulation.py` toxicity series to sum signed taker flow by side with maker/taker disambiguation.
- Add `adverse_selection_bps_by_fill_type` in simulation summary.

## Hour 5–7 — Latency realism + cancel-before-arrival robustness
Targets: `app/simulation.py`, `app/exchange/latency.py`
- Introduce `LatencyDistribution` with log-normal jitter around `feed_ms`, `order_entry_ms`, `cancel_ms`.
- Add exchange processing-time variance bucket; model TCP retransmit / drop tails as small cancellation probability.
- Replace coarse `arrival_time_ms` clamp with monotonic microsecond timestamps; validate no event can arrive before it was requested.

## Hour 7–9 — Market impact + borrow/locate realism
Targets: `app/break_test/costs.py`, `app/exchange/market.py`
- Add temporary impact decay function: `temp_bps(t, size) = theta1 * size + theta2 * sqrt(size) * exp(-lambda * t)`.
- Add short-locate failure probability as function of outstanding short inventory vs HTB supply curve: `locate_fail_p = sigmoid((short_inventory - htb_supply) / scale)`.
- Update fee schedule to tier on trade-count and notional simultaneously; record per-order fee in `Trade`.

## Hour 9–11 — TCA + market disruption attribution
Targets: `app/break_test/metrics.py`, `app/simulation.py`
- Expand `compute_tca_metrics()` to return timing cost, market cost, and operator cost components using participation-rate decomposition.
- Add attribution for liquidity withdrawal / forced liquidation events by tagging trades with `regime_label`.
- Add child-order execution profile: participation by step, passive vs aggressive ratio.

## Hour 11–12 — Verification + docs
Targets: `tests/test_exchange_realism.py`, `docs/exchange_realism_audit.md`
- Add golden-run determinism test: replay `run_simulation` twice; assert identical `ledger_digest`.
- Add stress scenarios in `test_exchange_realism.py`: 90% filled + remaining, empty book, 1000 queue depth, locate failure spike.
- Update `docs/exchange_realism_audit.md` with closed gaps, residual risks, and deferred items.
