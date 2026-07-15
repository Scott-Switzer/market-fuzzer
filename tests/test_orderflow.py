import random

from app.exchange import Account, Exchange
from app.orderflow import QueueReactiveProvider
from app.schemas import WorldSpec
from app.simulation import run_simulation
from app.world import build_demo_world


def calibrated_world(seed: int = 11) -> WorldSpec:
    data = build_demo_world(seed).model_dump()
    data.update(
        world_type="emergent_calibrated",
        calibration_pack_id="cal-demo",
        calibration_parameter_set_id="cal-demo-accepted-01",
        order_flow_provider="queue_reactive",
        order_flow_parameters={"base_order_size": 60.0, "flow_persistence": 0.4},
    )
    data["interventions"] = {
        "participation_rate": 0.05,
        "displayed_depth_multiplier": 0.5,
        "forced_seller_quantity": 1_000,
        "labels": ["depth_reduction", "forced_seller"],
    }
    data["experiment"]["participation_rate"] = 0.05
    return WorldSpec.model_validate(data)


def test_queue_reactive_world_is_deterministic_and_labeled_emergent():
    first = run_simulation(calibrated_world())
    second = run_simulation(calibrated_world())
    assert first.result_hash == second.result_hash
    assert first.summary["response_classification"] == "observed emergent simulation output"
    assert any(order["agent_id"].startswith("queue-reactive") for order in first.orders)
    assert any(event.get("order_flow_event_type") for event in first.events)


def test_sparse_book_uses_hierarchical_backoff():
    spec = calibrated_world()
    provider = QueueReactiveProvider(spec)
    exchange = Exchange([asset.ticker for asset in spec.assets], spec.exchange)
    for account_id in provider.account_ids:
        exchange.register(Account(account_id, 1_000_000, {}))
    actions = provider.actions(0, "NOVA", exchange, random.Random(3))
    assert actions
    assert actions[0].backoff_level == 2


def test_structural_world_never_claims_emergent_response():
    result = run_simulation(build_demo_world(4))
    assert result.summary["response_classification"] == "imposed structural assumption"
