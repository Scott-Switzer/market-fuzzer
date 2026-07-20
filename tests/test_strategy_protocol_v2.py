import pytest

from app.strategy_protocol import (
    StrategyActionV2,
    StrategyObservationV2,
    StrategyOpenOrderV2,
    parse_strategy_action,
    parse_strategy_observation,
)


def test_v2_protocol_exposes_only_own_order_lifecycle_and_parses_explicit_versions() -> None:
    observation = StrategyObservationV2(
        session_id="sealed-campaign",
        step=2,
        symbol="NOVA",
        side="buy",
        mid_ticks=100,
        best_bid_ticks=99,
        best_ask_ticks=101,
        spread_bps=200.0,
        observed_volume=10,
        inventory=5,
        remaining_quantity=0,
        exchange_latency_profile="normal",
        intervention_active=False,
        open_orders=(
            StrategyOpenOrderV2(
                order_id="strategy-order-1", side="buy", remaining_quantity=5, limit_price_ticks=90
            ),
        ),
    )
    assert type(parse_strategy_observation(observation.model_dump(mode="json"))) is StrategyObservationV2
    action = StrategyActionV2(
        action_type="replace", order_id="strategy-order-1", quantity=4, limit_price_ticks=91
    )
    assert type(parse_strategy_action(action.model_dump(mode="json"))) is StrategyActionV2


@pytest.mark.parametrize(
    "payload",
    [
        {"action_type": "submit", "order_type": "limit", "side": "buy", "quantity": 1},
        {"action_type": "cancel", "order_id": "strategy-order-1", "quantity": 1},
        {"action_type": "replace", "order_id": "strategy-order-1", "quantity": 1},
    ],
)
def test_v2_protocol_rejects_ambiguous_lifecycle_actions(payload: dict) -> None:
    with pytest.raises(ValueError):
        StrategyActionV2.model_validate(payload)
