from __future__ import annotations

import pytest

from app.benchmark.model import FillRecord, SessionResult, TaskKind, TaskSpec
from app.benchmark.tasks import evaluate, max_drawdown_cents

_FOCUS = "SYN001"


def _spec(kind: TaskKind, **overrides: object) -> TaskSpec:
    values = {
        "kind": kind,
        "focus_symbol": _FOCUS,
        "side": "buy",
        "target_quantity": 10_000,
        "agent_instruments": (_FOCUS,),
        "initial_position_per_instrument": 0,
        "max_order_quantity": 5_000,
    }
    values.update(overrides)
    return TaskSpec(**values)  # type: ignore[arg-type]


def _fill(quantity: int, price: int, *, side: str = "buy", maker: bool = False, tick: int = 1) -> FillRecord:
    return FillRecord(
        instrument_id=_FOCUS,
        side=side,
        quantity=quantity,
        price_ticks=price,
        step_index=0,
        day_index=0,
        is_maker=maker,
        notional_cents=quantity * price * tick,
    )


def _result(
    *,
    fills: tuple[FillRecord, ...] = (),
    arrival: int = 10_000,
    final: int = 10_000,
    violations: tuple[str, ...] = (),
    cash: int = 0,
    positions: dict[str, int] | None = None,
    peak: dict[str, int] | None = None,
    equity: tuple[int, ...] = (0, 0),
    final_value: int = 10_000_000,
    tick_size_cents: int = 1,
) -> SessionResult:
    buy_quantity = sum(f.quantity for f in fills if f.side == "buy")
    sell_quantity = sum(f.quantity for f in fills if f.side == "sell")
    return SessionResult(
        world_id="w",
        universe_id="u",
        holdout="public",
        sessions=("2026-06-01",),
        ledger_digest="ledger",
        market_logical_sha256="market",
        action_digest="actions",
        event_count=1,
        order_count=1,
        cancel_count=0,
        replace_count=0,
        trade_count=len(fills),
        background_trade_count=0,
        expired_day_order_count=0,
        instruments=(_FOCUS,),
        focus_symbol=_FOCUS,
        steps_total=10,
        tick_size_cents=tick_size_cents,
        arrival_price_ticks=arrival,
        final_price_ticks=final,
        agent_fills=fills,
        agent_maker_fill_count=sum(1 for fill in fills if fill.is_maker),
        agent_taker_fill_count=sum(1 for fill in fills if not fill.is_maker),
        agent_filled_quantity=sum(fill.quantity for fill in fills),
        agent_net_delivered_quantity=buy_quantity - sell_quantity,
        agent_cash_cents=cash,
        agent_positions=positions or {_FOCUS: 0},
        agent_peak_inventory=peak or {_FOCUS: 0},
        agent_initial_value_cents=10_000_000,
        agent_final_value_cents=final_value,
        equity_curve_cents=equity,
        quote_uptime=1.0,
        violations=violations,
        agent_failure=None,
        agent_failure_count=0,
        scoreable=True,
    )


# -- execution: parent-order completion -----------------------------------------


def test_full_completion_at_arrival_price_is_maximal_shortfall_credit() -> None:
    outcome = evaluate(_spec(TaskKind.EXECUTION), _result(fills=(_fill(10_000, 10_000),)))
    assert outcome.metrics["completion"] == 1.0
    assert outcome.metrics["implementation_shortfall_bps"] == 0.0
    assert outcome.score == 100.0


def test_no_fills_earns_no_execution_credit() -> None:
    outcome = evaluate(_spec(TaskKind.EXECUTION), _result())
    assert outcome.metrics["completion"] == 0.0
    assert outcome.score == 0.0


def test_a_round_trip_cannot_fake_completion() -> None:
    fills = (_fill(10_000, 10_000), _fill(10_000, 10_050, side="sell"))
    outcome = evaluate(_spec(TaskKind.EXECUTION), _result(fills=fills))
    assert outcome.metrics["net_delivered_quantity"] == 0
    assert outcome.metrics["completion"] == 0.0
    assert outcome.score == 0.0


def test_buying_the_target_then_selling_half_scores_fifty_percent() -> None:
    fills = (_fill(10_000, 10_000), _fill(5_000, 10_000, side="sell"))
    outcome = evaluate(_spec(TaskKind.EXECUTION), _result(fills=fills))
    assert outcome.metrics["net_delivered_quantity"] == 5_000
    assert outcome.metrics["completion"] == 0.5


def test_buying_the_target_only_scores_full_completion() -> None:
    outcome = evaluate(_spec(TaskKind.EXECUTION), _result(fills=(_fill(10_000, 10_000),)))
    assert outcome.metrics["completion"] == 1.0


def test_an_overfill_attempt_cannot_exceed_full_completion() -> None:
    outcome = evaluate(_spec(TaskKind.EXECUTION), _result(fills=(_fill(50_000, 10_000),)))
    assert outcome.metrics["completion"] == 1.0
    assert outcome.score <= 100.0


def test_shortfall_penalizes_a_paying_up_agent() -> None:
    cheap = evaluate(_spec(TaskKind.EXECUTION), _result(fills=(_fill(10_000, 10_010),)))
    dear = evaluate(_spec(TaskKind.EXECUTION), _result(fills=(_fill(10_000, 10_100),)))
    assert dear.metrics["implementation_shortfall_bps"] > cheap.metrics["implementation_shortfall_bps"]
    assert dear.score < cheap.score


def test_more_completion_scores_higher_at_equal_price() -> None:
    full = evaluate(_spec(TaskKind.EXECUTION), _result(fills=(_fill(10_000, 10_000),)))
    half = evaluate(_spec(TaskKind.EXECUTION), _result(fills=(_fill(5_000, 10_000),)))
    assert half.score < full.score


def test_the_net_cost_basis_reflects_an_intermediate_sale() -> None:
    # Buy 10,000 @ 10,000 then sell 5,000 @ 11,000; the remaining 5,000 were
    # effectively acquired at a net cost of 9,000 ticks.
    fills = (_fill(10_000, 10_000), _fill(5_000, 11_000, side="sell"))
    outcome = evaluate(_spec(TaskKind.EXECUTION), _result(fills=fills))
    assert outcome.metrics["average_price_ticks"] == pytest.approx(9_000.0)


def test_constraint_violations_reduce_execution_score() -> None:
    clean = evaluate(_spec(TaskKind.EXECUTION), _result(fills=(_fill(10_000, 10_000),)))
    dirty = evaluate(
        _spec(TaskKind.EXECUTION),
        _result(fills=(_fill(10_000, 10_000),), violations=("agent_order_rejected:risk",)),
    )
    assert dirty.score == clean.score - 25.0


def test_a_sell_execution_signs_shortfall_the_other_way() -> None:
    spec = _spec(TaskKind.EXECUTION, side="sell")
    better = evaluate(spec, _result(fills=(_fill(10_000, 10_100, side="sell"),)))
    worse = evaluate(spec, _result(fills=(_fill(10_000, 9_900, side="sell"),)))
    assert better.score > worse.score


def test_execution_reports_peak_inventory_not_final_inventory() -> None:
    fills = (_fill(9_000, 10_000), _fill(9_000, 10_050, side="sell"))
    outcome = evaluate(
        _spec(TaskKind.EXECUTION),
        _result(fills=fills, positions={_FOCUS: 0}, peak={_FOCUS: 9_000}),
    )
    assert outcome.metrics["max_inventory_quantity"] == 9_000
    assert outcome.metrics["net_delivered_quantity"] == 0


# -- market making ---------------------------------------------------------------


def test_market_making_score_uses_uptime_and_pnl() -> None:
    good = evaluate(
        _spec(TaskKind.MARKET_MAKING),
        _result(final_value=11_000_000, equity=(10_000_000, 11_000_000)),
    )
    bad = evaluate(
        _spec(TaskKind.MARKET_MAKING),
        _result(final_value=9_000_000, equity=(10_000_000, 9_000_000)),
    )
    assert good.score > bad.score
    assert 0.0 <= good.score <= 100.0


def test_market_making_uses_peak_inventory() -> None:
    outcome = evaluate(
        _spec(TaskKind.MARKET_MAKING),
        _result(positions={_FOCUS: 0}, peak={_FOCUS: 1_500_000}),
    )
    assert outcome.metrics["max_inventory_quantity"] == 1_500_000


def test_maker_share_is_computed_from_attributed_fills() -> None:
    fills = (_fill(100, 10_000, maker=True), _fill(100, 10_000, maker=False))
    outcome = evaluate(_spec(TaskKind.MARKET_MAKING), _result(fills=fills))
    assert outcome.metrics["maker_share"] == pytest.approx(0.5)


# -- portfolio -------------------------------------------------------------------


def test_portfolio_score_is_higher_for_a_better_risk_adjusted_return() -> None:
    winner = evaluate(
        _spec(TaskKind.PORTFOLIO),
        _result(final_value=10_500_000, equity=(10_000_000, 10_500_000)),
    )
    loser = evaluate(
        _spec(TaskKind.PORTFOLIO),
        _result(final_value=9_500_000, equity=(10_000_000, 9_500_000)),
    )
    assert winner.score > loser.score


def test_turnover_uses_account_currency_not_ticks() -> None:
    # One fill of 1,000 shares at 10,000 ticks with a 5-cent tick is 50,000,000
    # cents of notional, i.e. 5x the naive tick-based figure.
    fills = (_fill(1_000, 10_000, tick=5),)
    outcome = evaluate(
        _spec(TaskKind.PORTFOLIO),
        _result(fills=fills, tick_size_cents=5, final_value=10_000_000),
    )
    assert outcome.metrics["turnover_fraction"] == pytest.approx(1_000 * 10_000 * 5 / 10_000_000)


def test_turnover_is_five_times_larger_with_a_five_cent_tick() -> None:
    one_cent = evaluate(_spec(TaskKind.PORTFOLIO), _result(fills=(_fill(1_000, 10_000, tick=1),)))
    five_cent = evaluate(
        _spec(TaskKind.PORTFOLIO),
        _result(fills=(_fill(1_000, 10_000, tick=5),), tick_size_cents=5),
    )
    assert five_cent.metrics["turnover_fraction"] == pytest.approx(5 * one_cent.metrics["turnover_fraction"])


# -- helpers ---------------------------------------------------------------------


def test_max_drawdown_is_peak_to_trough() -> None:
    assert max_drawdown_cents((100, 120, 90, 110)) == 30
    assert max_drawdown_cents((100, 90, 80)) == 20
    assert max_drawdown_cents((100, 110, 120)) == 0
    assert max_drawdown_cents(()) == 0


def test_first_step_loss_is_captured_by_drawdown() -> None:
    # The curve begins at the pre-decision value, so an immediate first-step
    # loss is inside the peak-to-trough window.
    curve = (10_000_000, 9_990_000, 9_995_000)
    assert max_drawdown_cents(curve) == 10_000


def test_task_spec_rejects_a_focus_symbol_outside_the_universe() -> None:
    with pytest.raises(ValueError):
        TaskSpec(
            kind=TaskKind.EXECUTION,
            focus_symbol="NOPE",
            side="buy",
            target_quantity=1,
            agent_instruments=(_FOCUS,),
            initial_position_per_instrument=0,
            max_order_quantity=10,
        )
