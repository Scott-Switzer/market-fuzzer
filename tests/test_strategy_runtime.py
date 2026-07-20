import hashlib
import io
import json
import subprocess

import pytest

from app.execution_store import ArenaStore
from app.strategy_runtime import (
    ContainerStrategyArtifactV1,
    ContainerStrategySessionV1,
    ContainerStreamingStrategySessionV1,
)


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


def _observation_v2() -> dict:
    return {**_observation(), "schema_version": "2.0", "open_orders": []}


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


def test_container_artifact_canonical_bytes_match_its_sealed_digest() -> None:
    artifact = _artifact()
    assert hashlib.sha256(artifact.canonical_bytes).hexdigest() == artifact.artifact_digest


def test_container_artifact_freeze_binds_execution_resource_controls() -> None:
    artifact = _artifact()
    assert (
        artifact.artifact_digest
        != ContainerStrategyArtifactV1(
            image_digest=artifact.image_digest,
            command=artifact.command,
            timeout_ms=artifact.timeout_ms + 1,
        ).artifact_digest
    )
    assert (
        artifact.artifact_digest
        != ContainerStrategyArtifactV1(
            image_digest=artifact.image_digest,
            command=artifact.command,
            timeout_ms=artifact.timeout_ms,
            memory_mb=artifact.memory_mb + 64,
        ).artifact_digest
    )
    assert (
        artifact.artifact_digest
        != ContainerStrategyArtifactV1(
            image_digest=artifact.image_digest,
            command=artifact.command,
            timeout_ms=artifact.timeout_ms,
            cpu_limit=0.5,
        ).artifact_digest
    )


def test_container_session_preserves_v2_protocol_on_success_and_failure(monkeypatch, tmp_path) -> None:
    store = ArenaStore(tmp_path / "v2.sqlite3")
    monkeypatch.setattr(
        "app.strategy_runtime.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, '{"schema_version":"2.0","action_type":"hold"}\n', ""
        ),
    )
    response = ContainerStrategySessionV1(
        _artifact(),
        response_recorder=store.record_strategy_response,
        response_lookup=store.find_strategy_response,
    ).decide(_observation_v2())
    assert response.action["schema_version"] == "2.0"
    monkeypatch.setattr(
        "app.strategy_runtime.subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError())
    )
    failed = ContainerStrategySessionV1(
        _artifact(),
        response_recorder=store.record_strategy_response,
        response_lookup=store.find_strategy_response,
    ).decide({**_observation_v2(), "step": 2})
    assert failed.action == {
        "schema_version": "2.0",
        "action_type": "hold",
        "rationale_code": "isolated_runner_failure",
        "side": None,
        "order_type": None,
        "quantity": 0,
        "limit_price_ticks": None,
        "order_id": None,
    }


def test_container_session_fails_closed_without_a_durable_response_journal(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.strategy_runtime.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, '{"action_type":"hold"}\n', ""),
    )
    with pytest.raises(RuntimeError, match="response journal"):
        ContainerStrategySessionV1(_artifact()).decide(_observation())


def test_container_session_journals_bad_output_as_deterministic_hold(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "app.strategy_runtime.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "not-json\n", ""),
    )
    store = ArenaStore(tmp_path / "bad-output.sqlite3")
    response = ContainerStrategySessionV1(
        _artifact(),
        response_recorder=store.record_strategy_response,
        response_lookup=store.find_strategy_response,
    ).decide(_observation())
    assert response.action["rationale_code"] == "isolated_runner_failure"


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


class _StreamingProcess:
    def __init__(self, output: str) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(output)
        self.terminated = 0
        self.killed = 0
        self.closed_stdin = False

    def poll(self):
        return None

    def terminate(self) -> None:
        self.terminated += 1

    def kill(self) -> None:
        self.killed += 1

    def wait(self, *, timeout: float) -> int:
        return 0


def test_streaming_session_reuses_one_no_egress_process_and_journals_each_response(
    monkeypatch, tmp_path
) -> None:
    process = _StreamingProcess('{"action_type":"hold"}\n{"action_type":"hold"}\n')
    seen: dict = {}
    monkeypatch.setattr(
        "app.strategy_runtime.subprocess.Popen",
        lambda command, **kwargs: seen.update(command=command, kwargs=kwargs) or process,
    )
    monkeypatch.setattr("app.strategy_runtime.select.select", lambda streams, *_: (streams, [], []))
    store = ArenaStore(tmp_path / "streaming.sqlite3")
    session = ContainerStreamingStrategySessionV1(
        _artifact(),
        response_recorder=store.record_strategy_response,
        response_lookup=store.find_strategy_response,
    )
    first = session.decide(_observation())
    second = session.decide({**_observation(), "step": 2})
    assert first.action["action_type"] == second.action["action_type"] == "hold"
    assert "--interactive" in seen["command"]
    assert "--pull" in seen["command"] and "never" in seen["command"]
    assert "--network" in seen["command"] and "none" in seen["command"]
    assert "--memory-swap" in seen["command"] and "256m" in seen["command"]
    assert seen["kwargs"]["stderr"] is subprocess.DEVNULL
    assert len(process.stdin.getvalue().splitlines()) == 2
    session.close()
    assert process.terminated == 1


def test_streaming_session_times_out_fail_closed_and_terminates_process(monkeypatch, tmp_path) -> None:
    process = _StreamingProcess("")
    monkeypatch.setattr("app.strategy_runtime.subprocess.Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr("app.strategy_runtime.select.select", lambda *_: ([], [], []))
    store = ArenaStore(tmp_path / "streaming-timeout.sqlite3")
    response = ContainerStreamingStrategySessionV1(
        _artifact(),
        response_recorder=store.record_strategy_response,
        response_lookup=store.find_strategy_response,
    ).decide(_observation_v2())
    assert response.action["schema_version"] == "2.0"
    assert response.action["rationale_code"] == "isolated_runner_failure"
    assert process.terminated == 1
