import json
import subprocess

import pytest

from app.execution_store import ArenaStore
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


def test_container_session_records_response_before_returning_action(monkeypatch, tmp_path) -> None:
    seen = {}

    def run(command, **kwargs):
        seen["command"], seen["kwargs"] = command, kwargs
        return subprocess.CompletedProcess(command, 0, json.dumps({"action_type": "hold"}) + "\n", "")

    monkeypatch.setattr("app.strategy_runtime.subprocess.run", run)
    store = ArenaStore(tmp_path / "runtime.sqlite3")
    response = ContainerStrategySessionV1(
        _artifact(),
        response_recorder=store.record_strategy_response,
        response_lookup=store.find_strategy_response,
    ).decide(_observation())
    assert response.action["action_type"] == "hold"
    assert "--network" in seen["command"] and "none" in seen["command"]
    assert "--read-only" in seen["command"] and "--cap-drop" in seen["command"]
    assert seen["kwargs"]["env"] == {"PATH": "/usr/bin:/bin"}
    assert len(response.idempotency_key) == len(response.response_digest) == 64
    persisted = store.strategy_response_record(response.idempotency_key)
    assert persisted["response_digest"] == response.response_digest
    assert store.record_strategy_response(response)["replayed"] is True


def test_container_session_fails_closed_without_a_durable_response_journal(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.strategy_runtime.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, '{"action_type":"hold"}\n', ""),
    )
    with pytest.raises(RuntimeError, match="response journal"):
        ContainerStrategySessionV1(_artifact()).decide(_observation())


def test_container_session_fails_closed_on_bad_output(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.strategy_runtime.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "not-json\n", ""),
    )
    with pytest.raises(RuntimeError, match="invalid action"):
        ContainerStrategySessionV1(
            _artifact(), response_recorder=lambda _: None, response_lookup=lambda _: None
        ).decide(_observation())


def test_container_session_records_failure_and_replays_without_rerunning(monkeypatch, tmp_path) -> None:
    store = ArenaStore(tmp_path / "recovery.sqlite3")
    calls = 0

    def fail(command, **kwargs):
        nonlocal calls
        calls += 1
        raise subprocess.TimeoutExpired(command, 0.1)

    monkeypatch.setattr("app.strategy_runtime.subprocess.run", fail)
    session = ContainerStrategySessionV1(
        _artifact(),
        response_recorder=store.record_strategy_response,
        response_lookup=store.find_strategy_response,
    )
    first = session.decide(_observation())
    second = session.decide(_observation())
    assert first.action["rationale_code"] == "isolated_runner_failure"
    assert second == first
    assert calls == 1
