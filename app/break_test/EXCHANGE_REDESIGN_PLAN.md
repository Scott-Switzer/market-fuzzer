# Exchange Kernel Redesign Plan

## Context
- Current V1 engine (`app/exchange/market.py`, `order_book.py`, `orders.py`) is single-asset per book but already multi-symbol capable via `Exchange.books`.
- V2 engine (`app/exchange/v2.py`, `v2_matching.py`) introduces immutable ledger, risk reservations, settlements, typed commands.
- Break-test uses `build_world()` in `app/break_test/exchange_fwd.py` which hardcodes 3 assets.
- `run_simulation()` in `app/simulation.py` already accepts `execution_decider` for the execution agent but break-test exchange mode needs a cleaner router hook.

## Goals
1. Extend order book routing to arbitrary N assets via existing `Exchange.books` mapping (no per-book changes required).
2. Add deterministic cross-asset margin checks at order entry using portfolio-wide notional limit.
3. Add minimal settlement primitive: trade date + settle date tracking, cash-in-advance reservation, delivery/claims ledger.
4. Wire execution decider into simulation loop cleanly via a `StrategyOrderRouter`.
5. Keep backward compatibility for existing 3-asset demos by using `min_length=3` and default `build_world` still producing 3 assets unless configured.
6. Add property-based invariants: cash conservation, inventory conservation, cross-asset exposure limit.

## API / Data Model Diffs

### `app/exchange/orders.py`
- New `SettlementInfo` dataclass:
  - `trade_date_step: int`
  - `settle_date_step: int | None = None`
  - `status: SettlementStatus = "unsettled"`
- Extend `Trade` with optional `settlement: SettlementInfo | None = None`.

### `app/exchange/market.py` (V1 Exchange)
- Add `max_notional_exposure_cents: int | None = None` to `Exchange.__init__`.
- Add `_portfolio_exposure_cents()` to compute `sum(|inventory| * mid_price)`.
- New method `cross_asset_margin_check(order)` raises `OrderRejectedError` if exposure exceeds limit.
- Add reservation hooks to `Account`:
  - `reserved_cash_cents`, `reserved_inventory: dict[str, int]`.
  - Methods: `available_cash_cents()`, `available_position(symbol)`.
- Modify `_settle` to accept settlement primitive fields and update claims.

### `app/exchange/v2_matching.py` (V2 Match)
- `AccountStateV2` already has reservations. Add max portfolio notional check in `MatchingExchangeV2.submit()`:
  - `assert_total_notional_after(order) <= max_notional_exposure`.
- Add `trade_date_ns`, `settle_date_ns` on `TradeV2`.

### `app/schemas/world.py`
- Add optional `max_notional_exposure_multiplier: float = Field(default=1.0, ge=0.1, le=10.0)` to `ExchangeSpec`.
- Keep `assets.min_length=3` for backward compatibility.

### `app/simulation.py`
- Introduce `StrategyOrderRouter` dataclass:
  - Fields: `decider: ExecutionDecider | None`, `lot_size: int`, `target_quantity: int`, `queued: list[Order]`.
  - Methods: `route(agent, context, observation) -> list[Order]`.
- Modify `run_simulation()` to accept optional `order_router: StrategyOrderRouter | None = None`.
- Decouple execution agent route logic: if `order_router` is provided, use it; otherwise fall back to current inline behavior.

### `app/break_test/exchange_fwd.py`
- Parameterize N assets via helper `generate_assets(target_asset, n)`.
- Keep old 3-asset path when `n == 3` for backward compatibility.
- Route provided `execution_decider` into `run_simulation(..., order_router=...)` in exchange forward test.

## Invariants
- Cash conservation: sum(account.cash) + fee_account == initial total cash after each trade.
- Inventory conservation: sum(account.inventory[symbol]) == initial total inventory per asset.
- Cross-asset exposure: total gross notional <= configured hurdle.
- Determinism: same seed + spec produces identical ledger hash.

## Backward Compatibility
- `build_world()` keeps signature compatible; defaults to 3 assets.
- `ExchangeSpec` new fields have defaults, so old YAML/JSON still parse.

## Files to Modify
1. `app/exchange/orders.py` — add SettlementInfo/SettlementStatus, extend Trade.
2. `app/exchange/market.py` — reservations, margin checks, settlement hooks.
3. `app/exchange/v2_matching.py` — portfolio notional check, trade settlement fields.
4. `app/schemas/world.py` — add `max_notional_exposure_multiplier`.
5. `app/simulation.py` — `StrategyOrderRouter`, wire into loop.
6. `app/break_test/exchange_fwd.py` — parameterize asset count, route decider.
7. `app/break_test/EXCHANGE_REDESIGN_PLAN.md` — this document.

## Testing
- Ensure `pytest tests/test_v2_matching.py tests/test_event_kernel_v2.py tests/test_strategy_protocol_v2.py tests/test_break_test.py` still pass.
- Add new tests for:
  - N-asset world generation.
  - Cross-asset margin rejection.
  - Settlement date tracking.
  - StrategyOrderRouter fallback compatibility.
