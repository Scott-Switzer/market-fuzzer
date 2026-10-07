from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.benchmark import port as port_module
from app.benchmark.port import (
    HttpJsonPort,
    InProcessPort,
    accumulate_port,
    crossing_limit_action,
    hold_action,
    passive_maker_port,
    submit_limit_action,
    twap_port,
)
from app.strategy_protocol import StrategyActionV2

_OBSERVATION: dict[str, Any] = {
    "schema_version": "2.0",
    "session_id": "bench-test",
    "step": 1,
    "symbol": "SYN001",
    "side": "buy",
    "mid_ticks": 10_000,
    "best_bid_ticks": 9_999,
    "best_ask_ticks": 10_001,
    "spread_bps": 2.0,
    "observed_volume": 0,
    "inventory": 0,
    "remaining_quantity": 5_000,
    "exchange_latency_profile": "normal",
    "intervention_active": False,
    "open_orders": (),
}


def test_crossing_limit_action_targets_the_touch() -> None:
    action = crossing_limit_action(_OBSERVATION, 100)
    assert action["action_type"] == "submit"
    assert action["limit_price_ticks"] == 10_003
    sell = crossing_limit_action({**_OBSERVATION, "side": "sell"}, 100)
    assert sell["limit_price_ticks"] == 9_997


def test_builtin_ports_emit_valid_v2_actions() -> None:
    for port in (
        twap_port(slice_quantity=500),
        accumulate_port(slice_quantity=500, max_shares_per_instrument=1_000),
        passive_maker_port(spread_ticks=3, quantity=200),
    ):
        action = port.decide(dict(_OBSERVATION))
        assert StrategyActionV2.model_validate(action).schema_version == "2.0"


def test_twap_holds_once_the_parent_order_is_done() -> None:
    port = twap_port(slice_quantity=500)
    assert port.decide({**_OBSERVATION, "remaining_quantity": 0})["action_type"] == "hold"


def test_passive_maker_cancels_when_it_has_too_many_quotes() -> None:
    port = passive_maker_port(spread_ticks=3, quantity=200, max_open_orders=2)
    observation = {
        **_OBSERVATION,
        "open_orders": (
            {"order_id": "a", "side": "buy", "remaining_quantity": 200, "limit_price_ticks": 9_997},
            {"order_id": "b", "side": "sell", "remaining_quantity": 200, "limit_price_ticks": 10_003},
        ),
    }
    action = port.decide(observation)
    assert action["action_type"] == "cancel"
    assert action["order_id"] == "a"


def test_in_process_port_rejects_a_malformed_action() -> None:
    port = InProcessPort("bad", lambda _observation: {"action_type": "nonsense"})
    with pytest.raises(ValueError):
        port.decide(dict(_OBSERVATION))


def test_http_port_rejects_a_host_outside_the_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FWF_BENCH_ADAPTER_ALLOWED_HOSTS", "127.0.0.1,localhost")
    with pytest.raises(ValueError):
        HttpJsonPort("http://evil.example.com/decide")


def test_http_port_fails_closed_to_a_hold_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    real_client = httpx.Client

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        port_module.httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs)
    )
    monkeypatch.setenv("FWF_BENCH_ADAPTER_ALLOWED_HOSTS", "127.0.0.1")
    port = HttpJsonPort("http://127.0.0.1:9000/decide")
    action = port.decide(dict(_OBSERVATION))
    assert action["action_type"] == "hold"
    assert action["rationale_code"] == "adapter_error"
    assert port.errors == 1
    port.close()


def test_http_port_round_trips_an_external_action(monkeypatch: pytest.MonkeyPatch) -> None:
    real_client = httpx.Client
    payload = submit_limit_action("buy", 250, 10_001, "external")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        port_module.httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs)
    )
    monkeypatch.setenv("FWF_BENCH_ADAPTER_ALLOWED_HOSTS", "127.0.0.1")
    port = HttpJsonPort("http://127.0.0.1:9000/decide")
    action = port.decide(dict(_OBSERVATION))
    assert action["action_type"] == "submit"
    assert action["quantity"] == 250
    assert port.errors == 0
    port.close()


def test_hold_action_is_parseable() -> None:
    assert StrategyActionV2.model_validate(hold_action()).action_type == "hold"
