import hashlib
import json

import pytest

from app.external_adapter import execute_registered_strategy
from app.strategy_lab import ExternalAdapterContract
from app.world import build_demo_world


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
