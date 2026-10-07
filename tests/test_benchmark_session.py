from __future__ import annotations

from datetime import date
from typing import Any

from app.benchmark.model import TaskKind
from app.benchmark.port import (
    InProcessPort,
    StrategyDecisionPort,
    hold_action,
    submit_limit_action,
    twap_port,
)
from app.benchmark.session import BenchmarkSession, SessionConfig
from app.benchmark.tasks import build_task_spec
from app.benchmark.universe import PUBLIC_PROFILE, build_universe
from app.market.calendar import trading_days


class RecordingPort:
    def __init__(self, inner: StrategyDecisionPort) -> None:
        self.name = "recording"
        self.inner = inner
        self.observations: list[dict[str, Any]] = []

    def decide(self, observation: dict[str, Any]) -> dict[str, Any]:
        self.observations.append(dict(observation))
        return self.inner.decide(observation)

    def close(self) -> None:
        self.inner.close()


def _universe(*, count: int = 4, sessions: int = 2, seed: int = 11):
    return build_universe(
        universe_id="u-session",
        world_id="w-session",
        seed=seed,
        profile=PUBLIC_PROFILE,
        security_count=count,
        sessions=tuple(trading_days(date(2026, 6, 1), date(2026, 6, 30))[:sessions]),
    )


def _run(kind: TaskKind, port: StrategyDecisionPort, *, count: int = 4, sessions: int = 2, steps: int = 6):
    universe = _universe(count=count, sessions=sessions)
    task = build_task_spec(kind, universe, target_quantity=2_000)
    session = BenchmarkSession(
        universe=universe,
        profile=PUBLIC_PROFILE,
        task=task,
        port=port,
        config=SessionConfig(steps_per_day=steps),
    )
    return session.run()


def test_a_session_is_byte_deterministic() -> None:
    first = _run(TaskKind.EXECUTION, twap_port(slice_quantity=500))
    second = _run(TaskKind.EXECUTION, twap_port(slice_quantity=500))
    assert first.ledger_digest == second.ledger_digest
    assert first.action_digest == second.action_digest
    assert first.agent_fills == second.agent_fills
    assert first.event_count == second.event_count
    assert first.arrival_price_ticks == second.arrival_price_ticks


def test_a_session_generates_events_and_trades() -> None:
    result = _run(TaskKind.EXECUTION, twap_port(slice_quantity=500))
    assert result.event_count > 0
    assert result.trade_count > 0
    assert result.steps_total == 12
    assert result.instruments == ("SYN001", "SYN002", "SYN003", "SYN004")


def test_the_agent_observation_contract_is_satisfied() -> None:
    port = RecordingPort(twap_port(slice_quantity=500))
    result = _run(TaskKind.EXECUTION, port)
    assert port.observations
    assert result.agent_fills
    for observation in port.observations:
        assert observation["schema_version"] == "2.0"
        assert observation["symbol"] == "SYN001"
        assert observation["mid_ticks"] > 0
        assert observation["spread_bps"] >= 0.0
        assert observation["remaining_quantity"] >= 0
        for order in observation["open_orders"]:
            assert order["limit_price_ticks"] > 0
            assert order["remaining_quantity"] > 0
    remaining = [int(item["remaining_quantity"]) for item in port.observations]
    assert remaining == sorted(remaining, reverse=True)


def test_holding_never_trades_the_agent_account() -> None:
    port = InProcessPort("always-hold", lambda _observation: hold_action())
    result = _run(TaskKind.EXECUTION, port)
    assert result.agent_fills == ()
    assert result.agent_filled_quantity == 0
    assert result.order_count == 0
    assert result.quote_uptime == 0.0


def test_an_aggressive_order_is_partially_filled_across_makers() -> None:
    state = {"done": False}

    def decide(observation: dict[str, Any]) -> dict[str, Any]:
        if state["done"]:
            return hold_action()
        state["done"] = True
        price = int(observation.get("best_ask_ticks") or observation["mid_ticks"])
        return submit_limit_action("buy", 5_000, price, "aggressive")

    result = _run(TaskKind.EXECUTION, InProcessPort("aggressive-once", decide))
    assert len(result.agent_fills) >= 2
    assert all(fill.quantity > 0 and fill.price_ticks > 0 for fill in result.agent_fills)
    assert result.agent_filled_quantity < 5_000


def test_an_unknown_lifecycle_order_id_is_a_violation() -> None:
    def decide(_observation: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "2.0",
            "action_type": "cancel",
            "order_id": "does-not-exist",
            "rationale_code": "bad_cancel",
        }

    result = _run(TaskKind.EXECUTION, InProcessPort("bad-cancel", decide))
    assert "agent_lifecycle_unknown_order_id" in result.violations
    assert result.cancel_count == 0


def test_market_making_quotes_and_reports_uptime() -> None:
    from app.benchmark.port import passive_maker_port

    result = _run(TaskKind.MARKET_MAKING, passive_maker_port(spread_ticks=3, quantity=200))
    assert 0.0 <= result.quote_uptime <= 1.0
    assert result.quote_uptime > 0.0
    assert result.agent_final_value_cents > 0


def test_portfolio_task_touches_every_instrument() -> None:
    from app.benchmark.port import accumulate_port

    port = RecordingPort(accumulate_port(slice_quantity=200, max_shares_per_instrument=1_000))
    result = _run(TaskKind.PORTFOLIO, port, count=4)
    symbols = {item["symbol"] for item in port.observations}
    assert symbols == {"SYN001", "SYN002", "SYN003", "SYN004"}
    assert result.steps_total == 12
    assert len(port.observations) == 4 * 12
