from __future__ import annotations

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


def _result(
    *,
    fills: tuple[FillRecord, ...] = (),
    arrival: int = 10_000,
    final: int = 10_000,
    violations: tuple[str, ...] = (),
    cash: int = 0,
    positions: dict[str, int] | None = None,
    equity: tuple[int, ...] = (0, 0),
    final_value: int = 10_000_000,
) -> SessionResult:
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
        instruments=(_FOCUS,),
        focus_symbol=_FOCUS,
        steps_total=10,
        arrival_price_ticks=arrival,
        final_price_ticks=final,
        agent_fills=fills,
        agent_maker_fill_count=sum(1 for fill in fills if fill.is_maker),
        agent_taker_fill_count=sum(1 for fill in fills if not fill.is_maker),
        agent_filled_quantity=sum(fill.quantity for fill in fills),
        agent_cash_cents=cash,
        agent_positions=positions or {_FOCUS: 0},
        agent_initial_value_cents=10_000_000,
        agent_final_value_cents=final_value,
        equity_curve_cents=equity,
        quote_uptime=1.0,
        violations=violations,
    )


def _fill(quantity: int, price: int, *, side: str = "buy", maker: bool = False) -> FillRecord:
    return FillRecord(
        instrument_id=_FOCUS,
        side=side,
        quantity=quantity,
        price_ticks=price,
        step_index=0,
        day_index=0,
        is_maker=maker,
    )


def test_full_completion_at_arrival_price_is_maximal_shortfall_credit() -> None:
    outcome = evaluate(_spec(TaskKind.EXECUTION), _result(fills=(_fill(10_000, 10_000),)))
    assert outcome.metrics["completion"] == 1.0
    assert outcome.metrics["implementation_shortfall_bps"] == 0.0
    assert outcome.score == 100.0


def test_no_fills_earns_no_execution_credit() -> None:
    outcome = evaluate(_spec(TaskKind.EXECUTION), _result())
    assert outcome.metrics["completion"] == 0.0
    assert outcome.score == 0.0


def test_shortfall_penalizes_a_paying_up_agent() -> None:
    cheap = evaluate(_spec(TaskKind.EXECUTION), _result(fills=(_fill(10_000, 10_010),)))
    dear = evaluate(_spec(TaskKind.EXECUTION), _result(fills=(_fill(10_000, 10_100),)))
    assert dear.metrics["implementation_shortfall_bps"] > cheap.metrics["implementation_shortfall_bps"]
    assert dear.score < cheap.score


def test_more_completion_scores_higher_at_equal_price() -> None:
    full = evaluate(_spec(TaskKind.EXECUTION), _result(fills=(_fill(10_000, 10_000),)))
    half = evaluate(_spec(TaskKind.EXECUTION), _result(fills=(_fill(5_000, 10_000),)))
    assert half.score < full.score


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


def test_max_drawdown_is_peak_to_trough() -> None:
    assert max_drawdown_cents((100, 120, 90, 110)) == 30
    assert max_drawdown_cents((100, 90, 80)) == 20
    assert max_drawdown_cents((100, 110, 120)) == 0
    assert max_drawdown_cents(()) == 0


def test_task_spec_rejects_a_focus_symbol_outside_the_universe() -> None:
    import pytest

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
