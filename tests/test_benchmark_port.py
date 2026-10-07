from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.benchmark import port as port_module
from app.benchmark.port import (
    AGENT_PROTOCOL,
    AGENT_UNAVAILABLE,
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


def _observation(**overrides: Any) -> dict[str, Any]:
    return {**_OBSERVATION, **overrides}


def _http_port(monkeypatch: pytest.MonkeyPatch, handler: Any) -> HttpJsonPort:
    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        port_module.httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs)
    )
    monkeypatch.setenv("FWF_BENCH_ADAPTER_ALLOWED_HOSTS", "127.0.0.1")
    return HttpJsonPort("http://127.0.0.1:9000/decide")


# -- action constructors ---------------------------------------------------------


def test_crossing_limit_action_targets_the_touch() -> None:
    action = crossing_limit_action(_OBSERVATION, 100)
    assert action["action_type"] == "submit"
    assert action["limit_price_ticks"] == 10_003
    sell = crossing_limit_action(_observation(side="sell"), 100)
    assert sell["limit_price_ticks"] == 9_997


def test_builtin_ports_emit_valid_v2_actions() -> None:
    for port in (
        twap_port(slice_quantity=500),
        accumulate_port(slice_quantity=500, max_shares_per_instrument=1_000),
        passive_maker_port(spread_ticks=3, quantity=200),
    ):
        action = port.decide(dict(_OBSERVATION))
        assert StrategyActionV2.model_validate(action).schema_version == "2.0"


# -- TWAP parent-order discipline -------------------------------------------------


def test_twap_holds_when_the_parent_order_is_delivered() -> None:
    port = twap_port(slice_quantity=500)
    assert port.decide(_observation(remaining_quantity=0))["action_type"] == "hold"


def test_twap_cancels_live_orders_when_the_parent_order_is_delivered() -> None:
    port = twap_port(slice_quantity=500)
    action = port.decide(
        _observation(
            remaining_quantity=0,
            open_orders=(
                {
                    "order_id": "live-1",
                    "side": "buy",
                    "remaining_quantity": 250,
                    "limit_price_ticks": 9_990,
                },
            ),
        )
    )
    assert action["action_type"] == "cancel"
    assert action["order_id"] == "live-1"


def test_twap_subtracts_outstanding_same_side_quantity() -> None:
    port = twap_port(slice_quantity=500)
    action = port.decide(
        _observation(
            remaining_quantity=5_000,
            open_orders=(
                {
                    "order_id": "live-1",
                    "side": "buy",
                    "remaining_quantity": 4_800,
                    "limit_price_ticks": 9_990,
                },
            ),
        )
    )
    assert action["action_type"] == "submit"
    assert action["quantity"] == 200


def test_twap_holds_when_live_orders_already_cover_the_remainder() -> None:
    port = twap_port(slice_quantity=500)
    action = port.decide(
        _observation(
            remaining_quantity=500,
            open_orders=(
                {
                    "order_id": "live-1",
                    "side": "buy",
                    "remaining_quantity": 500,
                    "limit_price_ticks": 9_990,
                },
            ),
        )
    )
    assert action["action_type"] == "hold"


def test_twap_cancels_the_largest_order_when_over_committed() -> None:
    port = twap_port(slice_quantity=500)
    action = port.decide(
        _observation(
            remaining_quantity=300,
            open_orders=(
                {
                    "order_id": "small",
                    "side": "buy",
                    "remaining_quantity": 100,
                    "limit_price_ticks": 9_990,
                },
                {
                    "order_id": "big",
                    "side": "buy",
                    "remaining_quantity": 400,
                    "limit_price_ticks": 9_989,
                },
            ),
        )
    )
    assert action["action_type"] == "cancel"
    assert action["order_id"] == "big"


def test_twap_ignores_opposite_side_open_orders() -> None:
    port = twap_port(slice_quantity=500)
    action = port.decide(
        _observation(
            remaining_quantity=5_000,
            open_orders=(
                {
                    "order_id": "sell-live",
                    "side": "sell",
                    "remaining_quantity": 4_000,
                    "limit_price_ticks": 10_100,
                },
            ),
        )
    )
    assert action["action_type"] == "submit"
    assert action["quantity"] == 500


# -- passive maker ---------------------------------------------------------------


def test_passive_maker_cancels_when_it_has_too_many_quotes() -> None:
    port = passive_maker_port(spread_ticks=3, quantity=200, max_open_orders=2)
    observation = _observation(
        open_orders=(
            {"order_id": "a", "side": "buy", "remaining_quantity": 200, "limit_price_ticks": 9_997},
            {"order_id": "b", "side": "sell", "remaining_quantity": 200, "limit_price_ticks": 10_003},
        )
    )
    action = port.decide(observation)
    assert action["action_type"] == "cancel"
    assert action["order_id"] == "a"


# -- in-process failure semantics -------------------------------------------------


def test_in_process_port_fails_closed_on_a_malformed_action() -> None:
    port = InProcessPort("bad", lambda _observation: {"action_type": "nonsense"})
    action = port.decide(dict(_OBSERVATION))
    assert action["action_type"] == "hold"
    assert port.first_failure == AGENT_PROTOCOL
    assert port.protocol_failures == 1


def test_in_process_port_fails_closed_when_the_callable_raises() -> None:
    def explode(_observation: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("boom")

    port = InProcessPort("exploding", explode)
    assert port.decide(dict(_OBSERVATION))["action_type"] == "hold"
    assert port.first_failure == AGENT_PROTOCOL


def test_a_healthy_in_process_port_records_no_failure() -> None:
    port = twap_port(slice_quantity=500)
    port.decide(dict(_OBSERVATION))
    assert port.first_failure is None
    assert port.errors == 0


# -- HTTP agent failure classification -------------------------------------------


def test_http_port_rejects_a_host_outside_the_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FWF_BENCH_ADAPTER_ALLOWED_HOSTS", "127.0.0.1,localhost")
    with pytest.raises(ValueError):
        HttpJsonPort("http://evil.example.com/decide")


def test_http_port_accepts_an_explicit_hold_as_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    port = _http_port(monkeypatch, lambda _request: httpx.Response(200, json=hold_action()))
    action = port.decide(dict(_OBSERVATION))
    assert action["action_type"] == "hold"
    assert port.first_failure is None
    assert port.errors == 0
    port.close()


def test_http_port_round_trips_an_external_action(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = submit_limit_action("buy", 250, 10_001, "external")
    port = _http_port(monkeypatch, lambda _request: httpx.Response(200, json=payload))
    action = port.decide(dict(_OBSERVATION))
    assert action["action_type"] == "submit"
    assert action["quantity"] == 250
    assert port.first_failure is None
    port.close()


def test_http_port_classifies_connection_refusal_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    port = _http_port(monkeypatch, handler)
    action = port.decide(dict(_OBSERVATION))
    assert action["action_type"] == "hold"
    assert port.first_failure == AGENT_UNAVAILABLE
    assert port.unavailable_failures == 1
    port.close()


def test_http_port_classifies_a_timeout_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    port = _http_port(monkeypatch, handler)
    assert port.decide(dict(_OBSERVATION))["action_type"] == "hold"
    assert port.first_failure == AGENT_UNAVAILABLE
    port.close()


def test_http_port_classifies_a_server_error_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _http_port(monkeypatch, lambda _request: httpx.Response(503, json={"error": "down"}))
    assert port.decide(dict(_OBSERVATION))["action_type"] == "hold"
    assert port.first_failure == AGENT_UNAVAILABLE
    port.close()


def test_http_port_classifies_a_client_error_as_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    port = _http_port(monkeypatch, lambda _request: httpx.Response(400, json={"error": "bad"}))
    assert port.decide(dict(_OBSERVATION))["action_type"] == "hold"
    assert port.first_failure == AGENT_PROTOCOL
    port.close()


def test_http_port_classifies_malformed_json_as_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    port = _http_port(monkeypatch, lambda _request: httpx.Response(200, content=b"{not json"))
    assert port.decide(dict(_OBSERVATION))["action_type"] == "hold"
    assert port.first_failure == AGENT_PROTOCOL
    port.close()


def test_http_port_classifies_an_oversized_response_as_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _http_port(
        monkeypatch,
        lambda _request: httpx.Response(200, content=b"x" * (port_module._MAX_RESPONSE_BYTES + 1)),
    )
    assert port.decide(dict(_OBSERVATION))["action_type"] == "hold"
    assert port.first_failure == AGENT_PROTOCOL
    port.close()


def test_http_port_classifies_a_wrong_protocol_version_as_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "schema_version": "1.0",
        "action_type": "hold",
        "rationale_code": "legacy_agent",
    }
    port = _http_port(monkeypatch, lambda _request: httpx.Response(200, json=payload))
    assert port.decide(dict(_OBSERVATION))["action_type"] == "hold"
    assert port.first_failure == AGENT_PROTOCOL
    port.close()


def test_http_port_classifies_an_invalid_action_schema_as_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"schema_version": "2.0", "action_type": "submit", "quantity": 0}
    port = _http_port(monkeypatch, lambda _request: httpx.Response(200, json=payload))
    assert port.decide(dict(_OBSERVATION))["action_type"] == "hold"
    assert port.first_failure == AGENT_PROTOCOL
    port.close()


def test_the_first_failure_kind_is_retained(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            httpx.Response(400, json={"error": "bad"}),
            httpx.Response(503, json={"error": "down"}),
        ]
    )
    port = _http_port(monkeypatch, lambda _request: next(responses))
    port.decide(dict(_OBSERVATION))
    port.decide(dict(_OBSERVATION))
    assert port.first_failure == AGENT_PROTOCOL
    assert port.errors == 2
    assert port.protocol_failures == 1
    assert port.unavailable_failures == 1
    port.close()


def test_hold_action_is_parseable() -> None:
    assert StrategyActionV2.model_validate(hold_action()).action_type == "hold"
