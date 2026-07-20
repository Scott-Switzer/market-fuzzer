"""Bounded strategy-adapter runtime for the Synthetic Market World.

The API never imports customer code.  A registered adapter is either one of the
deterministic built-ins or a small HTTP JSON service reached through an explicit
host allowlist.  Both paths receive the same versioned observation and must
return the same versioned action contract.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from app.execution_arena import run_policy_on_compiled_world
from app.schemas import WorldSpec
from app.strategy_lab import ExternalAdapterContract
from app.strategy_protocol import StrategyActionV1, StrategyObservationV1
from app.strategy_runtime import (
    ContainerStrategyArtifactV1,
    ContainerStrategySessionV1,
    StrategyResponseRecordV1,
)

SUPPORTED_POLICY_IDS = frozenset({"twap", "aggressive_pov", "guarded_pov", "completion_first"})
_MAX_ADAPTER_RESPONSE_BYTES = 64 * 1024


def _legacy_http_adapter_enabled() -> bool:
    """HTTP callbacks are a development bridge, never the default production runtime."""
    return os.getenv("ARENA_ALLOW_LEGACY_HTTP_ADAPTER", "").strip().lower() in {"1", "true", "yes"}


def _contract_hash(contract: dict[str, Any]) -> str:
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _allowed_adapter_endpoint(endpoint_url: str) -> str:
    parsed = urlparse(endpoint_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("HTTP adapters require an http(s) endpoint with a hostname")
    if parsed.username or parsed.password:
        raise ValueError("adapter endpoint credentials must be supplied through auth_env_var")
    if parsed.fragment:
        raise ValueError("adapter endpoint must not contain a URL fragment")
    configured_hosts = {
        host.strip().lower()
        for host in os.getenv("ARENA_ADAPTER_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
        if host.strip()
    }
    if parsed.hostname.lower() not in configured_hosts:
        raise ValueError(f"adapter host {parsed.hostname!r} is not in ARENA_ADAPTER_ALLOWED_HOSTS")
    return parsed.hostname.lower()


def _observation_payload(observation: dict[str, Any]) -> dict[str, Any]:
    """Project engine diagnostics onto the public adapter observation contract."""

    return StrategyObservationV1.model_validate(
        {
            "schema_version": "1.0",
            "session_id": observation["session_id"],
            "step": observation["step"],
            "symbol": observation["symbol"],
            "side": observation["side"],
            "mid_ticks": observation["mid_ticks"],
            "best_bid_ticks": observation.get("best_bid_ticks"),
            "best_ask_ticks": observation.get("best_ask_ticks"),
            "spread_bps": observation["spread_bps"],
            "observed_volume": observation["observed_volume"],
            "inventory": observation["inventory"],
            "remaining_quantity": observation["remaining_quantity"],
            "exchange_latency_profile": observation["exchange_latency_profile"],
            "intervention_active": observation["intervention_active"],
        }
    ).model_dump(mode="json")


def _http_decider(contract: ExternalAdapterContract) -> tuple[httpx.Client, Any, str]:
    """Build an allowlisted HTTP adapter callback and its audit metadata."""

    assert contract.endpoint_url is not None
    endpoint_url = contract.endpoint_url
    host = _allowed_adapter_endpoint(endpoint_url)
    headers: dict[str, str] = {"content-type": "application/json", "accept": "application/json"}
    if contract.auth_env_var:
        token = os.getenv(contract.auth_env_var, "")
        if not token:
            raise ValueError(f"adapter auth environment variable {contract.auth_env_var!r} is empty")
        headers["authorization"] = f"Bearer {token}"
    client = httpx.Client(timeout=contract.timeout_ms / 1_000, headers=headers)

    def decide(observation: dict[str, Any]) -> dict[str, Any]:
        payload = _observation_payload(observation)
        try:
            body = bytearray()
            with client.stream("POST", endpoint_url, json=payload) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_ADAPTER_RESPONSE_BYTES:
                        raise ValueError(
                            f"HTTP adapter response exceeds {_MAX_ADAPTER_RESPONSE_BYTES} byte limit"
                        )
            action = StrategyActionV1.model_validate(json.loads(body))
        except Exception:
            if contract.error_policy == "reject_action":
                return StrategyActionV1(
                    action_type="hold", rationale_code="adapter_rejected_action"
                ).model_dump(mode="json")
            raise
        return action.model_dump(mode="json")

    return client, decide, host


def execute_registered_strategy(
    strategy: dict[str, Any],
    world: WorldSpec,
    *,
    source_world_hash: str,
    scenario_pack_id: str,
    response_recorder: Callable[[StrategyResponseRecordV1], object] | None = None,
) -> dict[str, Any]:
    """Execute one registered strategy through the bounded runtime contract."""

    strategy_type = str(strategy.get("strategy_type"))
    contract = strategy.get("external_adapter")
    client: httpx.Client | None = None
    execution_decider = None
    reported_policy_id: str | None = None
    if strategy_type == "external_adapter":
        if not isinstance(contract, dict):
            raise ValueError("external adapter strategy is missing its persisted contract")
        parsed = ExternalAdapterContract.model_validate(contract)
        expected_hash = _contract_hash(parsed.model_dump(mode="json"))
        if strategy.get("adapter_hash") != expected_hash:
            raise ValueError("external adapter contract hash does not match the registered strategy")
        policy_id = str(parsed.policy_id)
        runtime: dict[str, Any] = {
            "adapter_id": parsed.adapter_id,
            "adapter_version": parsed.adapter_version,
            "contract_hash": expected_hash,
            "network_access": parsed.adapter_id == "http_json_v1",
            "user_code_execution": parsed.adapter_id == "http_json_v1",
            "request_schema": "strategy_observation_v1",
            "response_schema": "execution_action_v1",
            "error_policy": parsed.error_policy,
        }
        if parsed.adapter_id == "http_json_v1":
            if not _legacy_http_adapter_enabled():
                raise ValueError(
                    "legacy HTTP adapters are disabled; production execution requires an isolated strategy runtime"
                )
            client, execution_decider, host = _http_decider(parsed)
            runtime.update(
                {
                    "execution_boundary": "bounded_http_adapter",
                    "production_eligible": False,
                    "endpoint_host": host,
                    "auth_env_var": parsed.auth_env_var,
                }
            )
            reported_policy_id = str(strategy.get("strategy_id") or policy_id)
        elif parsed.adapter_id == "container_jsonl_v1":
            if response_recorder is None:
                raise ValueError("container strategies require durable response recording before execution")
            artifact = ContainerStrategyArtifactV1(
                image_digest=str(parsed.image_digest),
                command=tuple(parsed.command or ()),
                timeout_ms=parsed.timeout_ms,
            )
            session = ContainerStrategySessionV1(artifact, response_recorder=response_recorder)

            def execution_decider(observation: dict[str, Any]) -> dict[str, Any]:
                return session.decide(_observation_payload(observation)).action

            runtime.update(
                {
                    "execution_boundary": "isolated_container_jsonl",
                    "network_access": False,
                    "user_code_execution": True,
                    "production_eligible": False,
                    "production_blockers": ("deterministic strategy crash recovery is not implemented",),
                    "strategy_artifact_digest": artifact.artifact_digest,
                }
            )
            reported_policy_id = str(strategy.get("strategy_id") or policy_id)
        else:
            runtime["execution_boundary"] = "allowlisted_in_process"
        try:
            row = run_policy_on_compiled_world(
                policy_id,
                world,
                source_world_hash=source_world_hash,
                scenario_pack_id=scenario_pack_id,
                execution_decider=execution_decider,
                reported_policy_id=reported_policy_id,
            )
        finally:
            if client is not None:
                client.close()
        return {
            **row,
            "strategy_adapter_id": str(strategy.get("strategy_id") or "unknown"),
            "adapter_runtime": runtime,
        }
    if strategy_type == "arena_policy":
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
        row = run_policy_on_compiled_world(
            policy_id,
            world,
            source_world_hash=source_world_hash,
            scenario_pack_id=scenario_pack_id,
        )
        return {
            **row,
            "strategy_adapter_id": str(strategy.get("strategy_id") or "unknown"),
            "adapter_runtime": runtime,
        }
    raise ValueError("strategy type is not executable by the bounded runtime")


def adapter_provenance(strategy: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    """Return the persisted, user-visible provenance for one execution cell."""

    return {
        "strategy_type": strategy["strategy_type"],
        "adapter_hash": strategy.get("adapter_hash"),
        "adapter_contract": strategy.get("external_adapter"),
        "adapter_runtime": runtime,
    }
