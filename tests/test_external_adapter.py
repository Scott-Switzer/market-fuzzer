import hashlib
import json
from typing import Literal

import pytest

from app.external_adapter import execute_registered_strategy
from app.simulation import run_simulation
from app.strategy_lab import ExternalAdapterContract
from app.strategy_protocol import StrategyActionV1
from app.strategy_runtime import StrategyResponseRecordV1
from app.world import build_demo_world


@pytest.fixture(autouse=True)
def _enable_legacy_http_adapter_for_explicit_legacy_tests(monkeypatch) -> None:
    monkeypatch.setenv("ARENA_ALLOW_LEGACY_HTTP_ADAPTER", "1")


def _contract() -> dict[str, str | int]:
    return ExternalAdapterContract(
        adapter_id="declarative_in_process_v1",
        adapter_version="1.0.0",
        policy_id="guarded_pov",
        input_observation_schema="market_observation_v1",
        output_action_schema="execution_action_v1",
        timeout_ms=250,
        error_policy="fail_cell",
    ).model_dump(mode="json")


def _strategy() -> dict:
    contract = _contract()
    return {
        "strategy_id": "strategy-test",
        "strategy_type": "external_adapter",
        "builtin_policy_id": "guarded_pov",
        "external_adapter": contract,
        "adapter_hash": hashlib.sha256(
            json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def test_bounded_adapter_executes_allowlisted_policy_with_runtime_provenance() -> None:
    row = execute_registered_strategy(
        _strategy(),
        build_demo_world(42),
        source_world_hash="world-hash",
        scenario_pack_id="scenario-pack-test",
    )
    assert row["execution_source"] == "compiled_scenario_pack"
    assert row["adapter_runtime"]["adapter_id"] == "declarative_in_process_v1"
    assert row["adapter_runtime"]["network_access"] is False
    assert row["adapter_runtime"]["user_code_execution"] is False


def test_bounded_adapter_rejects_tampered_contract_provenance() -> None:
    strategy = _strategy()
    strategy["external_adapter"]["timeout_ms"] = 999
    with pytest.raises(ValueError, match="contract hash"):
        execute_registered_strategy(
            strategy,
            build_demo_world(42),
            source_world_hash="world-hash",
            scenario_pack_id="scenario-pack-test",
        )


def test_http_adapter_fails_closed_without_explicit_legacy_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("ARENA_ALLOW_LEGACY_HTTP_ADAPTER")
    contract = ExternalAdapterContract(
        adapter_id="http_json_v1",
        adapter_version="1.0.0",
        policy_id="guarded_pov",
        input_observation_schema="market_observation_v1",
        output_action_schema="execution_action_v1",
        timeout_ms=250,
        endpoint_url="http://127.0.0.1:9100/decide",
    ).model_dump(mode="json")
    strategy = {**_strategy(), "external_adapter": contract}
    strategy["adapter_hash"] = hashlib.sha256(
        json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(ValueError, match="disabled"):
        execute_registered_strategy(
            strategy,
            build_demo_world(42),
            source_world_hash="world-hash",
            scenario_pack_id="scenario-pack-test",
        )


def test_http_adapter_executes_versioned_observation_and_action(monkeypatch) -> None:
    contract = ExternalAdapterContract(
        adapter_id="http_json_v1",
        adapter_version="1.0.0",
        policy_id="guarded_pov",
        input_observation_schema="market_observation_v1",
        output_action_schema="execution_action_v1",
        timeout_ms=250,
        error_policy="fail_cell",
        endpoint_url="http://127.0.0.1:9100/decide",
    ).model_dump(mode="json")
    strategy = {
        "strategy_id": "strategy-http-test",
        "strategy_type": "external_adapter",
        "builtin_policy_id": "guarded_pov",
        "external_adapter": contract,
        "adapter_hash": hashlib.sha256(
            json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    seen: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            return None

        def iter_bytes(self):
            return iter(
                [
                    json.dumps(
                        StrategyActionV1(
                            action_type="market", side="buy", quantity=10, rationale_code="test_adapter"
                        ).model_dump(mode="json")
                    ).encode()
                ]
            )

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            seen["client_kwargs"] = kwargs

        def stream(self, method: str, url: str, *, json: dict) -> FakeResponse:
            seen["url"] = url
            seen["observation"] = json
            return FakeResponse()

        def close(self) -> None:
            seen["closed"] = True

    def fake_run(policy_id, world, **kwargs):
        seen["policy_id"] = policy_id
        action = kwargs["execution_decider"](
            {
                "session_id": "world:execution-agent",
                "step": 3,
                "symbol": "NOVA",
                "side": "buy",
                "mid_ticks": 100,
                "best_bid_ticks": 99,
                "best_ask_ticks": 101,
                "spread_bps": 200.0,
                "observed_volume": 120,
                "inventory": 0,
                "remaining_quantity": 100,
                "exchange_latency_profile": "high",
                "intervention_active": True,
            }
        )
        seen["action"] = action
        return {"policy_id": kwargs["reported_policy_id"]}

    monkeypatch.setattr("app.external_adapter.httpx.Client", FakeClient)
    monkeypatch.setattr("app.external_adapter.run_policy_on_compiled_world", fake_run)
    row = execute_registered_strategy(
        strategy,
        build_demo_world(42),
        source_world_hash="world-hash",
        scenario_pack_id="scenario-pack-test",
    )
    assert seen["url"] == "http://127.0.0.1:9100/decide"
    assert seen["observation"]["schema_version"] == "1.0"
    assert seen["observation"]["remaining_quantity"] == 100
    assert seen["action"]["rationale_code"] == "test_adapter"
    assert seen["closed"] is True
    assert row["policy_id"] == "strategy-http-test"
    assert row["adapter_runtime"]["execution_boundary"] == "bounded_http_adapter"
    assert row["adapter_runtime"]["network_access"] is True


def test_http_adapter_rejects_oversized_response(monkeypatch) -> None:
    contract = ExternalAdapterContract(
        adapter_id="http_json_v1",
        adapter_version="1.0.0",
        policy_id="guarded_pov",
        input_observation_schema="market_observation_v1",
        output_action_schema="execution_action_v1",
        timeout_ms=250,
        error_policy="fail_cell",
        endpoint_url="http://127.0.0.1:9100/decide",
    ).model_dump(mode="json")
    strategy = {
        "strategy_id": "strategy-http-large-response",
        "strategy_type": "external_adapter",
        "external_adapter": contract,
        "adapter_hash": hashlib.sha256(
            json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }

    class OversizedResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            return iter([b"x" * (64 * 1024 + 1)])

    class OversizedClient:
        def __init__(self, **kwargs) -> None:
            return None

        def stream(self, method: str, url: str, *, json: dict) -> OversizedResponse:
            return OversizedResponse()

        def close(self) -> None:
            return None

    def fake_run(policy_id, world, **kwargs):
        kwargs["execution_decider"](
            {
                "session_id": "world:execution-agent",
                "step": 3,
                "symbol": "NOVA",
                "side": "buy",
                "mid_ticks": 100,
                "best_bid_ticks": 99,
                "best_ask_ticks": 101,
                "spread_bps": 200.0,
                "observed_volume": 120,
                "inventory": 0,
                "remaining_quantity": 100,
                "exchange_latency_profile": "high",
                "intervention_active": True,
            }
        )
        return {"policy_id": policy_id}

    monkeypatch.setattr("app.external_adapter.httpx.Client", OversizedClient)
    monkeypatch.setattr("app.external_adapter.run_policy_on_compiled_world", fake_run)
    with pytest.raises(ValueError, match="exceeds 65536 byte limit"):
        execute_registered_strategy(
            strategy,
            build_demo_world(42),
            source_world_hash="world-hash",
            scenario_pack_id="scenario-pack-test",
        )


def test_container_adapter_uses_isolated_runtime_without_premature_production_claim(monkeypatch) -> None:
    contract = ExternalAdapterContract(
        adapter_id="container_jsonl_v1",
        adapter_version="1.0.0",
        policy_id="guarded_pov",
        input_observation_schema="market_observation_v1",
        output_action_schema="execution_action_v1",
        timeout_ms=250,
        image_digest="registry.example/strategy@sha256:" + "a" * 64,
        command=("/runner",),
    ).model_dump(mode="json")
    strategy = {
        "strategy_id": "strategy-container-test",
        "strategy_type": "external_adapter",
        "external_adapter": contract,
        "adapter_hash": hashlib.sha256(
            json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    seen: dict = {}

    def fake_decide(session, observation: dict) -> StrategyResponseRecordV1:
        seen["observation"] = observation
        action = StrategyActionV1(action_type="hold").model_dump(mode="json")
        response = StrategyResponseRecordV1("key", "artifact", "request", "response", action)
        assert session.response_recorder is not None
        session.response_recorder(response)
        return response

    def fake_run(policy_id, world, **kwargs):
        seen["action"] = kwargs["execution_decider"](
            {
                "session_id": "world:execution-agent",
                "step": 3,
                "symbol": "NOVA",
                "side": "buy",
                "mid_ticks": 100,
                "best_bid_ticks": 99,
                "best_ask_ticks": 101,
                "spread_bps": 200.0,
                "observed_volume": 120,
                "inventory": 0,
                "remaining_quantity": 100,
                "exchange_latency_profile": "high",
                "intervention_active": True,
            }
        )
        return {"policy_id": kwargs["reported_policy_id"]}

    monkeypatch.setattr("app.external_adapter.ContainerStrategySessionV1.decide", fake_decide)
    monkeypatch.setattr("app.external_adapter.run_policy_on_compiled_world", fake_run)
    recorded: list[StrategyResponseRecordV1] = []
    row = execute_registered_strategy(
        strategy,
        build_demo_world(42),
        source_world_hash="world-hash",
        scenario_pack_id="scenario-pack-test",
        response_recorder=recorded.append,
        response_lookup=lambda _: None,
    )
    assert seen["observation"]["schema_version"] == "1.0"
    assert seen["action"]["action_type"] == "hold"
    assert row["policy_id"] == "strategy-container-test"
    assert row["adapter_runtime"]["execution_boundary"] == "isolated_container_jsonl"
    assert row["adapter_runtime"]["network_access"] is False
    assert row["adapter_runtime"]["production_eligible"] is True
    assert row["adapter_runtime"]["production_blockers"] == ()
    assert recorded[0].action["action_type"] == "hold"


def test_http_adapter_reject_action_policy_holds_on_protocol_error(monkeypatch) -> None:
    contract = ExternalAdapterContract(
        adapter_id="http_json_v1",
        adapter_version="1.0.0",
        policy_id="guarded_pov",
        input_observation_schema="market_observation_v1",
        output_action_schema="execution_action_v1",
        timeout_ms=250,
        error_policy="reject_action",
        endpoint_url="http://127.0.0.1:9100/decide",
    ).model_dump(mode="json")
    strategy = {
        "strategy_id": "strategy-http-reject-action",
        "strategy_type": "external_adapter",
        "external_adapter": contract,
        "adapter_hash": hashlib.sha256(
            json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }

    class BadResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            return iter([b"not-json"])

    class BadClient:
        def __init__(self, **kwargs) -> None:
            return None

        def stream(self, method: str, url: str, *, json: dict) -> BadResponse:
            return BadResponse()

        def close(self) -> None:
            return None

    def fake_run(policy_id, world, **kwargs):
        return {
            "action": kwargs["execution_decider"](
                {
                    "session_id": "world:execution-agent",
                    "step": 3,
                    "symbol": "NOVA",
                    "side": "buy",
                    "mid_ticks": 100,
                    "best_bid_ticks": 99,
                    "best_ask_ticks": 101,
                    "spread_bps": 200.0,
                    "observed_volume": 120,
                    "inventory": 0,
                    "remaining_quantity": 100,
                    "exchange_latency_profile": "high",
                    "intervention_active": True,
                }
            )
        }

    monkeypatch.setattr("app.external_adapter.httpx.Client", BadClient)
    monkeypatch.setattr("app.external_adapter.run_policy_on_compiled_world", fake_run)
    row = execute_registered_strategy(
        strategy,
        build_demo_world(42),
        source_world_hash="world-hash",
        scenario_pack_id="scenario-pack-test",
    )
    assert row["action"]["action_type"] == "hold"
    assert row["action"]["rationale_code"] == "adapter_rejected_action"


def test_http_adapter_reaches_real_simulation_with_side_and_lot_contract(monkeypatch) -> None:
    contract = ExternalAdapterContract(
        adapter_id="http_json_v1",
        adapter_version="1.0.0",
        policy_id="guarded_pov",
        input_observation_schema="market_observation_v1",
        output_action_schema="execution_action_v1",
        timeout_ms=250,
        error_policy="fail_cell",
        endpoint_url="http://127.0.0.1:9100/decide",
    ).model_dump(mode="json")
    strategy = {
        "strategy_id": "strategy-http-real-simulation",
        "strategy_type": "external_adapter",
        "external_adapter": contract,
        "adapter_hash": hashlib.sha256(
            json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    seen: list[dict] = []

    class Response:
        def __init__(self, side: Literal["buy", "sell"]) -> None:
            self.body = json.dumps(
                StrategyActionV1(
                    action_type="market", side=side, quantity=13, rationale_code="real_simulation"
                ).model_dump(mode="json")
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            return iter([self.body])

    class Client:
        def __init__(self, **kwargs) -> None:
            return None

        def stream(self, method: str, url: str, *, json: dict) -> Response:
            seen.append(json)
            return Response(json["side"])

        def close(self) -> None:
            return None

    monkeypatch.setattr("app.external_adapter.httpx.Client", Client)
    world_data = build_demo_world(42).model_dump(mode="python")
    world_data["exchange"]["lot_size"] = 10
    world_data["experiment"]["parent_order"]["quantity"] = 1_000
    from app.schemas import WorldSpec

    row = execute_registered_strategy(
        strategy,
        WorldSpec.model_validate(world_data),
        source_world_hash="world-hash",
        scenario_pack_id="scenario-pack-test",
    )
    assert row["adapter_runtime"]["execution_boundary"] == "bounded_http_adapter"
    assert seen
    assert {item["side"] for item in seen} == {"buy"}
    assert all(item["schema_version"] == "1.0" for item in seen)


def test_http_adapter_rejects_non_allowlisted_endpoint() -> None:
    contract = ExternalAdapterContract(
        adapter_id="http_json_v1",
        adapter_version="1.0.0",
        policy_id="guarded_pov",
        input_observation_schema="market_observation_v1",
        output_action_schema="execution_action_v1",
        timeout_ms=250,
        error_policy="fail_cell",
        endpoint_url="https://example.com/decide",
    ).model_dump(mode="json")
    strategy = {
        "strategy_id": "strategy-http-blocked",
        "strategy_type": "external_adapter",
        "external_adapter": contract,
        "adapter_hash": hashlib.sha256(
            json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    with pytest.raises(ValueError, match="not in ARENA_ADAPTER_ALLOWED_HOSTS"):
        execute_registered_strategy(
            strategy,
            build_demo_world(42),
            source_world_hash="world-hash",
            scenario_pack_id="scenario-pack-test",
        )


def test_execution_decider_controls_exchange_orders_inside_simulator() -> None:
    observations: list[dict] = []

    def hold_decider(observation: dict) -> dict:
        observations.append(observation)
        return StrategyActionV1(action_type="hold").model_dump(mode="json")

    result = run_simulation(build_demo_world(42), execution_decider=hold_decider)
    execution_orders = [order for order in result.orders if order["agent_id"] == "execution-01"]
    assert observations
    assert len(observations) == len(result.strategy_observations)
    assert execution_orders == []
    assert {row["adapter_action"]["action_type"] for row in result.strategy_observations} == {"hold"}


def test_execution_decider_reads_side_and_rounds_to_exchange_lot_size() -> None:
    world_data = build_demo_world(42).model_dump(mode="python")
    world_data["exchange"]["lot_size"] = 10
    world_data["experiment"]["parent_order"]["quantity"] = 1_000
    from app.schemas import WorldSpec

    world = WorldSpec.model_validate(world_data)
    seen_sides: list[str] = []

    def market_decider(observation: dict) -> dict:
        seen_sides.append(observation["side"])
        return StrategyActionV1(action_type="market", side=observation["side"], quantity=13).model_dump(
            mode="json"
        )

    result = run_simulation(world, execution_decider=market_decider)
    execution_orders = [order for order in result.orders if order["agent_id"] == "execution-01"]
    assert seen_sides
    assert set(seen_sides) == {"buy"}
    assert execution_orders
    assert all(order["quantity"] % 10 == 0 for order in execution_orders)
