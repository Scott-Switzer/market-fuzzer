from app.compiler import compile_prompt
from app.engine import run_scenario_battery, run_world


def test_compiler_creates_a_reproducible_spec():
    spec = compile_prompt("Create thin liquidity with an earnings shock and 2,500 shares", seed=9)
    assert spec.seed == 9
    assert spec.parent_order_shares == 2500
    assert spec.scenario == "liquidity_withdrawal"


def test_world_replays_from_a_seed():
    spec = compile_prompt("Create a normal market", seed=11)
    assert run_world(spec).to_dict() == run_world(spec).to_dict()


def test_battery_runs_all_required_worlds():
    result = run_scenario_battery(compile_prompt("Create a normal market"))
    assert [run["spec"]["scenario"] for run in result["runs"]] == [
        "normal", "liquidity_withdrawal", "earnings_shock", "crowded_unwind"
    ]
