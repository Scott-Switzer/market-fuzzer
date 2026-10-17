"""Hot-path bookkeeping must stay equal to a full trade-log scan."""

from __future__ import annotations

from app.simulation import run_simulation
from app.world import build_demo_world


def test_timeline_step_volume_matches_trade_log() -> None:
    world = build_demo_world(11)
    result = run_simulation(
        world,
        collect_timeline=True,
        collect_agent_states=False,
        collect_strategy_steps=False,
    )
    again = run_simulation(
        world,
        collect_timeline=True,
        collect_agent_states=False,
        collect_strategy_steps=False,
    )
    assert result.summary["ledger_digest"] == again.summary["ledger_digest"]
    assert result.summary["filled_quantity"] == again.summary["filled_quantity"]

    recorded: dict[str, int] = {}
    for frame in result.timeline:
        for symbol, state in frame["asset_states"].items():
            recorded[symbol] = recorded.get(symbol, 0) + int(state["volume"])
    scanned: dict[str, int] = {}
    for trade in result.trades:
        symbol = str(trade["symbol"])
        scanned[symbol] = scanned.get(symbol, 0) + int(trade["quantity"])
    for symbol, quantity in scanned.items():
        assert recorded.get(symbol, 0) == quantity
    for symbol, quantity in recorded.items():
        assert quantity == scanned.get(symbol, 0)

    slim = run_simulation(
        world,
        collect_timeline=False,
        collect_agent_states=False,
        collect_strategy_steps=False,
    )
    assert slim.summary["ledger_digest"] == result.summary["ledger_digest"]
    assert slim.summary["filled_quantity"] == result.summary["filled_quantity"]
    assert slim.timeline == []
