from copy import deepcopy

import pytest

from app.exchange import Account, Exchange, Order, OrderType, Side
from app.execution_arena import benchmark_matrix, run_execution_challenge
from app.schemas import ExchangeSpec, WorldSpec
from app.simulation import run_simulation
from app.world import build_demo_world, mutate_scenario


def test_queue_and_fill_evidence_preserves_price_time_order() -> None:
    exchange = Exchange(["NOVA"], ExchangeSpec(lot_size=10, maker_fee_bps=0, taker_fee_bps=0))
    for agent_id in ("seller-1", "seller-2", "buyer"):
        exchange.register(Account(agent_id, 10_000_000, {"NOVA": 0}))
    first = Order("sell-1", "seller-1", "NOVA", Side.SELL, OrderType.LIMIT, 100, 0, 101)
    second = Order("sell-2", "seller-2", "NOVA", Side.SELL, OrderType.LIMIT, 100, 0, 101)
    exchange.submit(first, 0)
    exchange.submit(second, 0)
    assert second.displayed_quantity_ahead_at_entry == 100

    trades = exchange.submit(Order("buy-1", "buyer", "NOVA", Side.BUY, OrderType.MARKET, 150, 1), 1)
    assert [trade.maker_order_id for trade in trades] == ["sell-1", "sell-2"]
    assert trades[1].maker_queue_ahead_at_entry == 100
    assert trades[1].quantity_traded_at_level_before_fill == 100
    assert trades[1].maker_partial_fill_sequence == 1
    assert trades[1].fill_sequence > trades[0].fill_sequence


def test_replay_has_monotonic_latency_and_step_accounting() -> None:
    result = run_execution_challenge("aggressive_pov", "normal", 42)
    replay = result["replay"]
    assert replay["strategy_activity"]
    assert replay["evidence_rows"]
    assert result["metrics"]["inventory_accounting_ties"] is True
    assert all(
        row["child_order_accounting_ties"]
        and row["parent_inventory_accounting_ties"]
        and row["strategy_inventory_accounting_ties"]
        for row in replay["strategy_activity"]
    )
    for order in replay["orders"]:
        times = [
            order["market_event_time_ms"],
            order["publication_time_ms"],
            order["observation_time_ms"],
            order["decision_time_ms"],
            order["submission_time_ms"],
        ]
        assert times == sorted(times)
        if order["exchange_arrival_time_ms"] is not None:
            assert order["exchange_arrival_time_ms"] >= order["submission_time_ms"]
    for trade in replay["strategy_trades"]:
        assert trade["fill_step"] >= trade["arrival_step"]
        assert trade["fill_time_ms"] >= trade["arrival_time_ms"]


def test_higher_latency_cannot_publish_or_arrive_earlier() -> None:
    low_data = build_demo_world(19).model_dump(mode="python")
    high_data = deepcopy(low_data)
    low_data["exchange"]["latency_profile"] = "low"
    high_data["exchange"]["latency_profile"] = "high"
    low = run_simulation(WorldSpec.model_validate(low_data))
    high = run_simulation(WorldSpec.model_validate(high_data))
    assert (
        high.strategy_observations[0]["observation_time_ms"]
        >= low.strategy_observations[0]["observation_time_ms"]
    )
    low_order = next(row for row in low.orders if row["agent_id"] == "execution-01")
    high_order = next(row for row in high.orders if row["agent_id"] == "execution-01")
    assert high_order["exchange_arrival_time_ms"] >= low_order["exchange_arrival_time_ms"]


def test_cancel_effective_time_cannot_precede_request_time() -> None:
    data = build_demo_world(29).model_dump(mode="python")
    data["exchange"]["latency_profile"] = "high"
    execution = next(
        population for population in data["agents"]["populations"] if population["type"] == "execution"
    )
    execution["parameters"]["cancel_after_ms"] = 10
    result = run_simulation(WorldSpec.model_validate(data))
    strategy_cancels = [row for row in result.cancels if row["agent_id"] == "execution-01"]
    assert strategy_cancels
    assert all(row["effective_time_ms"] >= row["request_time_ms"] for row in strategy_cancels)


def test_higher_transaction_cost_cannot_improve_buyer_net_execution_cost() -> None:
    def buyer_cost(taker_fee_bps: int) -> int:
        exchange = Exchange(["NOVA"], ExchangeSpec(lot_size=10, maker_fee_bps=0, taker_fee_bps=taker_fee_bps))
        exchange.register(Account("seller", 10_000_000, {"NOVA": 100}))
        exchange.register(Account("buyer", 10_000_000, {"NOVA": 0}))
        starting_cash = exchange.accounts["buyer"].cash_cents
        exchange.submit(Order("sell", "seller", "NOVA", Side.SELL, OrderType.LIMIT, 100, 0, 10_000), 0)
        exchange.submit(Order("buy", "buyer", "NOVA", Side.BUY, OrderType.MARKET, 100, 1), 1)
        return starting_cash - exchange.accounts["buyer"].cash_cents

    assert buyer_cost(10) >= buyer_cost(1)


def test_reducing_displayed_liquidity_does_not_increase_initial_depth() -> None:
    deep_data = build_demo_world(23).model_dump(mode="python")
    thin_data = deepcopy(deep_data)
    thin_data["interventions"]["displayed_depth_multiplier"] = 0.3
    deep = run_simulation(WorldSpec.model_validate(deep_data))
    thin = run_simulation(WorldSpec.model_validate(thin_data))
    deep_depth = deep.timeline[0]["asset_states"]["NOVA"]["ask_depth"]
    thin_depth = thin.timeline[0]["asset_states"]["NOVA"]["ask_depth"]
    assert thin_depth <= deep_depth


def test_removing_scheduled_event_removes_its_direct_record() -> None:
    event_world, _ = mutate_scenario(build_demo_world(31), "earnings_shock")
    without_event = event_world.model_copy(update={"events": []})
    activated = run_simulation(event_world)
    removed = run_simulation(without_event)
    assert any(event["type"] == "earnings" for event in activated.events)
    assert not any(event["type"] == "earnings" for event in removed.events)
    assert activated.result_hash != removed.result_hash


def test_environment_hash_is_policy_independent_but_policy_hash_is_not() -> None:
    aggressive = run_execution_challenge("aggressive_pov", "normal", 42)
    guarded = run_execution_challenge("guarded_pov", "normal", 42)
    assert aggressive["world"]["environment_hash"] == guarded["world"]["environment_hash"]
    assert aggressive["world"]["policy_specification_hash"] != guarded["world"]["policy_specification_hash"]


@pytest.mark.parametrize("seeds", [(41,), (42,), (41, 42)])
def test_rank_reversal_holds_across_deterministic_seed_groups(seeds: tuple[int, ...]) -> None:
    rows = {row["policy_id"]: row for row in benchmark_matrix(seeds=seeds)["rows"]}
    assert rows["aggressive_pov"]["public_rank"] == 1
    assert rows["guarded_pov"]["robustness_rank"] == 1
    assert rows["aggressive_pov"]["robustness_rank"] > 1
