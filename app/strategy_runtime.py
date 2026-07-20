"""Digest-pinned, no-egress container strategy session primitives."""

from __future__ import annotations

import hashlib
import json
import select
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.strategy_protocol import failure_hold_action, parse_strategy_action, parse_strategy_observation

_MAX_MESSAGE_BYTES = 64 * 1024


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ContainerStrategyArtifactV1:
    image_digest: str
    command: tuple[str, ...]
    timeout_ms: int
    memory_mb: int = 256
    cpu_limit: float = 1.0

    def __post_init__(self) -> None:
        if "@sha256:" not in self.image_digest or len(self.image_digest.rsplit("@sha256:", 1)[1]) != 64:
            raise ValueError("strategy image must be pinned to a sha256 digest")
        if not self.command or self.timeout_ms < 1 or self.memory_mb < 64 or self.cpu_limit <= 0:
            raise ValueError("strategy artifact has invalid resource limits")

    @property
    def artifact_digest(self) -> str:
        return _digest(self._frozen_definition)

    @property
    def canonical_bytes(self) -> bytes:
        """Exact frozen-artifact preimage required by sealed campaign registration."""
        return json.dumps(self._frozen_definition, sort_keys=True, separators=(",", ":")).encode()

    @property
    def _frozen_definition(self) -> dict[str, object]:
        """Every execution-affecting artifact control belongs in the freeze preimage."""
        return {
            "image_digest": self.image_digest,
            "command": self.command,
            "timeout_ms": self.timeout_ms,
            "memory_mb": self.memory_mb,
            "cpu_limit": self.cpu_limit,
        }


@dataclass(frozen=True, slots=True)
class StrategyResponseRecordV1:
    idempotency_key: str
    artifact_digest: str
    request_digest: str
    response_digest: str
    action: dict[str, Any]


class ContainerStrategySessionV1:
    """Execute one bounded decision without importing or networking customer code."""

    def __init__(
        self,
        artifact: ContainerStrategyArtifactV1,
        *,
        response_recorder: Callable[[StrategyResponseRecordV1], object] | None = None,
        response_lookup: Callable[[str], StrategyResponseRecordV1 | None] | None = None,
    ) -> None:
        self.artifact = artifact
        self.response_recorder = response_recorder
        self.response_lookup = response_lookup

    def decide(self, observation: dict[str, Any]) -> StrategyResponseRecordV1:
        parsed_observation = parse_strategy_observation(observation)
        public = parsed_observation.model_dump(mode="json")
        request_digest = _digest(public)
        idempotency_key = _digest({"artifact": self.artifact.artifact_digest, "request": request_digest})
        if self.response_recorder is None or self.response_lookup is None:
            raise RuntimeError("isolated strategy response journal is required before order admission")
        recovered = self.response_lookup(idempotency_key)
        if recovered is not None:
            if (
                recovered.artifact_digest != self.artifact.artifact_digest
                or recovered.request_digest != request_digest
                or recovered.idempotency_key != idempotency_key
            ):
                raise RuntimeError("persisted strategy response conflicts with current request")
            parse_strategy_action(recovered.action)
            return recovered
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            f"{self.artifact.memory_mb}m",
            "--cpus",
            str(self.artifact.cpu_limit),
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--user",
            "65534:65534",
            self.artifact.image_digest,
            *self.artifact.command,
        ]
        try:
            completed = subprocess.run(
                command,
                input=json.dumps(public, separators=(",", ":")) + "\n",
                capture_output=True,
                text=True,
                timeout=self.artifact.timeout_ms / 1_000,
                check=True,
                env={"PATH": "/usr/bin:/bin"},
            )
        except (OSError, subprocess.SubprocessError):
            action = failure_hold_action(parsed_observation)
            return self._record(idempotency_key, request_digest, action)
        try:
            if len(completed.stdout.encode()) > _MAX_MESSAGE_BYTES:
                raise ValueError("isolated strategy response exceeds message budget")
            lines = [line for line in completed.stdout.splitlines() if line.strip()]
            if len(lines) != 1:
                raise ValueError("isolated strategy must emit exactly one JSONL response")
            action = parse_strategy_action(json.loads(lines[0])).model_dump(mode="json")
        except (json.JSONDecodeError, ValueError):
            action = failure_hold_action(parsed_observation)
        return self._record(idempotency_key, request_digest, action)

    def _record(
        self, idempotency_key: str, request_digest: str, action: dict[str, Any]
    ) -> StrategyResponseRecordV1:
        record = StrategyResponseRecordV1(
            idempotency_key=idempotency_key,
            artifact_digest=self.artifact.artifact_digest,
            request_digest=request_digest,
            response_digest=_digest(action),
            action=action,
        )
        assert self.response_recorder is not None
        self.response_recorder(record)
        return record


class ContainerStreamingStrategySessionV1:
    """One no-egress JSONL container process for a bounded sealed strategy session.

    The process is started lazily, receives exactly one observation per line, and
    must return exactly one action line before the per-decision deadline.  A
    protocol failure, timeout, or process failure is journaled as a deterministic
    hold and tears down the process, preventing stale output from a failed stream
    from being admitted on a later observation.
    """

    def __init__(
        self,
        artifact: ContainerStrategyArtifactV1,
        *,
        response_recorder: Callable[[StrategyResponseRecordV1], object] | None = None,
        response_lookup: Callable[[str], StrategyResponseRecordV1 | None] | None = None,
    ) -> None:
        self.artifact = artifact
        self.response_recorder = response_recorder
        self.response_lookup = response_lookup
        self._process: subprocess.Popen[str] | None = None

    def __enter__(self) -> ContainerStreamingStrategySessionV1:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def decide(self, observation: dict[str, Any]) -> StrategyResponseRecordV1:
        parsed_observation = parse_strategy_observation(observation)
        public = parsed_observation.model_dump(mode="json")
        request_digest = _digest(public)
        idempotency_key = _digest({"artifact": self.artifact.artifact_digest, "request": request_digest})
        if self.response_recorder is None or self.response_lookup is None:
            raise RuntimeError("isolated strategy response journal is required before order admission")
        recovered = self.response_lookup(idempotency_key)
        if recovered is not None:
            if (
                recovered.artifact_digest != self.artifact.artifact_digest
                or recovered.request_digest != request_digest
                or recovered.idempotency_key != idempotency_key
            ):
                raise RuntimeError("persisted strategy response conflicts with current request")
            parse_strategy_action(recovered.action)
            return recovered
        try:
            process = self._ensure_started()
            if process.stdin is None:
                raise RuntimeError("isolated strategy process has no stdin")
            process.stdin.write(json.dumps(public, separators=(",", ":")) + "\n")
            process.stdin.flush()
            action = self._read_action(process, parsed_observation)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            self.close()
            action = failure_hold_action(parsed_observation)
        return self._record(idempotency_key, request_digest, action)

    def close(self) -> None:
        """Release the container process deterministically; safe to invoke repeatedly."""
        process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=max(0.1, self.artifact.timeout_ms / 1_000))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=max(0.1, self.artifact.timeout_ms / 1_000))
            else:
                process.wait(timeout=max(0.1, self.artifact.timeout_ms / 1_000))
        except (OSError, ValueError, subprocess.SubprocessError):
            # The runner already has a deterministic journal record for every
            # admitted response; cleanup failures cannot reopen the boundary.
            return

    def _ensure_started(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        self.close()
        self._process = subprocess.Popen(
            self._command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env={"PATH": "/usr/bin:/bin"},
        )
        return self._process

    def _command(self) -> list[str]:
        return [
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            f"{self.artifact.memory_mb}m",
            "--memory-swap",
            f"{self.artifact.memory_mb}m",
            "--cpus",
            str(self.artifact.cpu_limit),
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--user",
            "65534:65534",
            self.artifact.image_digest,
            *self.artifact.command,
        ]

    def _read_action(self, process: subprocess.Popen[str], observation: Any) -> dict[str, Any]:
        if process.stdout is None:
            raise RuntimeError("isolated strategy process has no stdout")
        timeout_seconds = self.artifact.timeout_ms / 1_000
        readable, _, _ = select.select([process.stdout], [], [], timeout_seconds)
        if not readable:
            raise RuntimeError("isolated strategy response timed out")
        line = process.stdout.readline(_MAX_MESSAGE_BYTES + 1)
        if not line or len(line.encode()) > _MAX_MESSAGE_BYTES:
            raise RuntimeError("isolated strategy response is missing or exceeds message budget")
        try:
            return parse_strategy_action(json.loads(line)).model_dump(mode="json")
        except (json.JSONDecodeError, ValueError) as error:
            raise RuntimeError("isolated strategy response violates JSONL protocol") from error

    def _record(
        self, idempotency_key: str, request_digest: str, action: dict[str, Any]
    ) -> StrategyResponseRecordV1:
        record = StrategyResponseRecordV1(
            idempotency_key=idempotency_key,
            artifact_digest=self.artifact.artifact_digest,
            request_digest=request_digest,
            response_digest=_digest(action),
            action=action,
        )
        assert self.response_recorder is not None
        self.response_recorder(record)
        return record
