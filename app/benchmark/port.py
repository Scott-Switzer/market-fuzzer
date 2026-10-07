"""External strategy decision ports for the synthetic exchange benchmark.

A port receives a ``StrategyObservationV2`` JSON document and must return a
``StrategyActionV2`` JSON document. Two implementations ship here:

* :class:`InProcessPort` wraps a deterministic Python callable (built-in
  baselines and tests).
* :class:`HttpJsonPort` posts observations to an external HTTP agent, guarded by
  an explicit host allowlist, and fails closed to a protocol-matched hold.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from app.strategy_protocol import StrategyActionV2, parse_strategy_action

__all__ = [
    "HttpJsonPort",
    "InProcessPort",
    "StrategyDecisionPort",
    "accumulate_port",
    "allowed_adapter_hosts",
    "crossing_limit_action",
    "hold_action",
    "passive_maker_port",
    "submit_limit_action",
    "twap_port",
]

_MAX_RESPONSE_BYTES = 64 * 1024


class StrategyDecisionPort(Protocol):
    name: str

    def decide(self, observation: dict[str, Any]) -> dict[str, Any]: ...

    def close(self) -> None: ...


def allowed_adapter_hosts() -> frozenset[str]:
    return frozenset(
        host.strip().lower()
        for host in os.getenv("FWF_BENCH_ADAPTER_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
        if host.strip()
    )


def hold_action(rationale_code: str = "builtin_hold") -> dict[str, Any]:
    return StrategyActionV2(action_type="hold", rationale_code=rationale_code).model_dump(mode="json")


def submit_limit_action(
    side: str, quantity: int, price_ticks: int, rationale_code: str = "builtin_submit"
) -> dict[str, Any]:
    return StrategyActionV2(
        action_type="submit",
        side=side,  # type: ignore[arg-type]
        order_type="limit",
        quantity=quantity,
        limit_price_ticks=max(1, int(price_ticks)),
        rationale_code=rationale_code,
    ).model_dump(mode="json")


def cancel_action(order_id: str, rationale_code: str = "builtin_cancel") -> dict[str, Any]:
    return StrategyActionV2(
        action_type="cancel", order_id=order_id, rationale_code=rationale_code
    ).model_dump(mode="json")


def crossing_limit_action(observation: dict[str, Any], quantity: int) -> dict[str, Any]:
    """A marketable limit price that crosses the observed touch."""

    side = str(observation["side"])
    reference = observation.get("best_ask_ticks") if side == "buy" else observation.get("best_bid_ticks")
    if reference is None:
        reference = observation.get("mid_ticks") or 1
    price = int(reference) + (2 if side == "buy" else -2)
    return submit_limit_action(side, quantity, max(1, price))


class InProcessPort:
    """Wrap a deterministic callable as a decision port."""

    def __init__(self, name: str, decide: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.name = name
        self._decide = decide
        self.errors = 0

    def decide(self, observation: dict[str, Any]) -> dict[str, Any]:
        return parse_strategy_action(self._decide(observation)).model_dump(mode="json")

    def close(self) -> None:
        return None


class HttpJsonPort:
    """Post observations to an allowlisted external HTTP agent."""

    def __init__(
        self,
        endpoint_url: str,
        *,
        timeout_ms: int = 10_000,
        auth_env_var: str | None = None,
        name: str = "http-agent",
    ) -> None:
        parsed = urlparse(endpoint_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("an HTTP agent requires an http(s) endpoint with a hostname")
        if parsed.username or parsed.password:
            raise ValueError("agent endpoint credentials must be supplied through auth_env_var")
        if parsed.hostname.lower() not in allowed_adapter_hosts():
            raise ValueError(f"agent host {parsed.hostname!r} is not in FWF_BENCH_ADAPTER_ALLOWED_HOSTS")
        headers = {"content-type": "application/json", "accept": "application/json"}
        if auth_env_var:
            token = os.getenv(auth_env_var, "")
            if not token:
                raise ValueError(f"agent auth environment variable {auth_env_var!r} is empty")
            headers["authorization"] = f"Bearer {token}"
        self._endpoint = endpoint_url
        self._client = httpx.Client(timeout=timeout_ms / 1_000, headers=headers)
        self.name = f"{name}:{parsed.hostname}"
        self.errors = 0

    def decide(self, observation: dict[str, Any]) -> dict[str, Any]:
        try:
            body = bytearray()
            with self._client.stream("POST", self._endpoint, json=observation) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_RESPONSE_BYTES:
                        raise ValueError("agent response exceeds the byte budget")
            action = parse_strategy_action(json.loads(body))
            if not isinstance(action, StrategyActionV2):
                raise ValueError("the benchmark agent must speak strategy action protocol version 2.0")
            return action.model_dump(mode="json")
        except Exception:
            self.errors += 1
            return hold_action("adapter_error")

    def close(self) -> None:
        self._client.close()


def twap_port(*, slice_quantity: int, name: str = "builtin-twap") -> InProcessPort:
    """Buy (or sell) a fixed slice of the remaining parent order every step."""

    def decide(observation: dict[str, Any]) -> dict[str, Any]:
        remaining = int(observation.get("remaining_quantity", 0))
        if remaining <= 0:
            return hold_action()
        quantity = min(remaining, max(1, slice_quantity))
        return crossing_limit_action(observation, quantity)

    return InProcessPort(name, decide)


def accumulate_port(
    *, slice_quantity: int, max_shares_per_instrument: int, name: str = "builtin-accumulate"
) -> InProcessPort:
    """A simple long-only portfolio baseline that accumulates a diversified book."""

    def decide(observation: dict[str, Any]) -> dict[str, Any]:
        inventory = int(observation.get("inventory", 0))
        if inventory >= max_shares_per_instrument:
            return hold_action()
        quantity = min(slice_quantity, max_shares_per_instrument - inventory)
        return crossing_limit_action(observation, max(1, quantity))

    return InProcessPort(name, decide)


def passive_maker_port(
    *, spread_ticks: int, quantity: int, max_open_orders: int = 2, name: str = "builtin-maker"
) -> InProcessPort:
    """A minimal passive market-making baseline: keep up to N quotes near mid."""

    def decide(observation: dict[str, Any]) -> dict[str, Any]:
        open_orders = tuple(observation.get("open_orders", ()))
        if len(open_orders) >= max_open_orders:
            return cancel_action(str(open_orders[0]["order_id"]))
        mid = int(observation.get("mid_ticks") or 1)
        side = (
            "buy"
            if sum(1 for order in open_orders if order["side"] == "buy")
            <= sum(1 for order in open_orders if order["side"] == "sell")
            else "sell"
        )
        price = mid - spread_ticks if side == "buy" else mid + spread_ticks
        return submit_limit_action(side, quantity, max(1, price))

    return InProcessPort(name, decide)
