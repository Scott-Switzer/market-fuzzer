"""Bounded, deterministic strategy-adapter runtime.

This module is deliberately small: registered strategies can only resolve to
the allow-listed in-process policies. It does not load user code, spawn a
process, make a network request, or claim support for arbitrary external
strategy runtimes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.execution_arena import run_policy_on_compiled_world
from app.schemas import WorldSpec
from app.strategy_lab import ExternalAdapterContract

SUPPORTED_POLICY_IDS = frozenset({"twap", "aggressive_pov", "guarded_pov", "completion_first"})


def _contract_hash(contract: dict[str, Any]) -> str:
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def execute_registered_strategy(
    strategy: dict[str, Any],
    world: WorldSpec,
    *,
    source_world_hash: str,
    scenario_pack_id: str,
) -> dict[str, Any]:
    """Execute one registered strategy through the bounded runtime contract."""

    strategy_type = str(strategy.get("strategy_type"))
    contract = strategy.get("external_adapter")
    if strategy_type == "external_adapter":
        if not isinstance(contract, dict):
            raise ValueError("external adapter strategy is missing its persisted contract")
        parsed = ExternalAdapterContract.model_validate(contract)
        expected_hash = _contract_hash(parsed.model_dump(mode="json"))
        if strategy.get("adapter_hash") != expected_hash:
            raise ValueError("external adapter contract hash does not match the registered strategy")
        policy_id = str(parsed.policy_id)
        runtime = {
            "adapter_id": parsed.adapter_id,
            "adapter_version": parsed.adapter_version,
            "contract_hash": expected_hash,
            "execution_boundary": "allowlisted_in_process",
            "network_access": False,
            "user_code_execution": False,
        }
    elif strategy_type == "arena_policy":
        if contract is not None:
            raise ValueError("arena policy strategy cannot carry an external adapter contract")
        policy_id = str(strategy.get("builtin_policy_id"))
        if policy_id not in SUPPORTED_POLICY_IDS:
            raise ValueError("strategy policy is not in the deterministic allowlist")
        runtime = {
            "adapter_id": "builtin_execution_policy",
            "adapter_version": "1.0.0",
            "contract_hash": None,
            "execution_boundary": "allowlisted_in_process",
            "network_access": False,
            "user_code_execution": False,
        }
    else:
        raise ValueError("strategy type is not executable by the bounded runtime")

    row = run_policy_on_compiled_world(
        str(policy_id),
        world,
        source_world_hash=source_world_hash,
        scenario_pack_id=scenario_pack_id,
    )
    return {**row, "adapter_runtime": runtime}


def adapter_provenance(strategy: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    """Return the persisted, user-visible provenance for one execution cell."""

    return {
        "strategy_type": strategy["strategy_type"],
        "adapter_hash": strategy.get("adapter_hash"),
        "adapter_contract": strategy.get("external_adapter"),
        "adapter_runtime": runtime,
    }
