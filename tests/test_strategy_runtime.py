import json
import subprocess

import pytest

from app.strategy_runtime import ContainerStrategyArtifactV1, ContainerStrategySessionV1


def _artifact() -> ContainerStrategyArtifactV1:
    return ContainerStrategyArtifactV1(
        image_digest="registry.example/strategy@sha256:" + "a" * 64,
        command=("/runner",),
        timeout_ms=100,
    )


def _observation() -> dict:
    return {
        "session_id": "session-1",
        "step": 1,
        "symbol": "NOVA",
        "side": "buy",
        "mid_ticks": 100,
        "best_bid_ticks": 99,
        "best_ask_ticks": 101,
        "spread_bps": 100.0,
        "observed_volume": 10,
        "inventory": 0,
        "remaining_quantity": 10,
        "exchange_latency_profile": "normal",
        "intervention_active": False,
    }


def test_container_session_uses_no_egress_digest_pinned_limits(monkeypatch) -> None:
    seen = {}

    def run(command, **kwargs):
        seen["command"], seen["kwargs"] = command, kwargs
        return subprocess.CompletedProcess(command, 0, json.dumps({"action_type": "hold"}) + "\n", "")

    monkeypatch.setattr("app.strategy_runtime.subprocess.run", run)
    response = ContainerStrategySessionV1(_artifact()).decide(_observation())
    assert response.action["action_type"] == "hold"
    assert "--network" in seen["command"] and "none" in seen["command"]
    assert "--read-only" in seen["command"] and "--cap-drop" in seen["command"]
    assert seen["kwargs"]["env"] == {"PATH": "/usr/bin:/bin"}
    assert len(response.idempotency_key) == len(response.response_digest) == 64


def test_container_session_fails_closed_on_bad_output(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.strategy_runtime.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "not-json\n", ""),
    )
    with pytest.raises(RuntimeError, match="invalid action"):
        ContainerStrategySessionV1(_artifact()).decide(_observation())
