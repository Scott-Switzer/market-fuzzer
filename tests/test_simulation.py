from app.simulation import run_simulation
from app.world import build_demo_world, mutate_scenario


def test_same_seed_is_deterministic_and_different_seed_changes_path():
    first = run_simulation(build_demo_world(13))
    replay = run_simulation(build_demo_world(13))
    different = run_simulation(build_demo_world(14))
    assert first.result_hash == replay.result_hash
    assert first.result_hash != different.result_hash


def test_three_assets_trade_and_prices_are_not_assigned_to_fundamentals():
    result = run_simulation(build_demo_world())
    states = result.timeline[-1]["asset_states"]
    assert set(states) == {"NOVA", "ORBT", "VYNE"}
    assert any(state["mid_ticks"] != state["fundamental_ticks"] for state in states.values())
    assert {row["symbol"] for row in result.trades} == {"NOVA", "ORBT", "VYNE"}


def test_scenario_mutations_use_common_seed_and_produce_expected_effects():
    base = build_demo_world(99)
    normal, _ = mutate_scenario(base, "normal")
    thin, _ = mutate_scenario(base, "liquidity_withdrawal")
    stressed, _ = mutate_scenario(base, "crowded_unwind")
    assert normal.seed == thin.seed == stressed.seed == 99
    thin_result = run_simulation(thin)
    stressed_result = run_simulation(stressed)
    before = thin_result.timeline[30]["asset_states"]["NOVA"]["ask_depth"]
    after = thin_result.timeline[80]["asset_states"]["NOVA"]["ask_depth"]
    assert after < before
    forced_sales = [trade for trade in stressed_result.trades if trade["seller_id"].startswith("forced_liquidator")]
    assert forced_sales


def test_larger_order_changes_execution_outcome_in_controlled_world():
    small_data = build_demo_world(22).model_dump()
    large_data = build_demo_world(22).model_dump()
    small_data["experiment"]["parent_order"]["quantity"] = 1_000
    large_data["experiment"]["parent_order"]["quantity"] = 12_000
    from app.schemas import WorldSpec
    small = run_simulation(WorldSpec.model_validate(small_data))
    large = run_simulation(WorldSpec.model_validate(large_data))
    assert large.summary["filled_quantity"] > small.summary["filled_quantity"]
    assert large.summary["implementation_shortfall_bps"] != small.summary["implementation_shortfall_bps"]
