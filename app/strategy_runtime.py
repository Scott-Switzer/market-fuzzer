"""Digest-pinned, no-egress container strategy session primitives."""

from __future__ import annotations

import hashlib
import json
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
        return _digest({"image_digest": self.image_digest, "command": self.command})

    @property
    def canonical_bytes(self) -> bytes:
        """Exact frozen-artifact preimage required by sealed campaign registration."""
        return json.dumps(
            {"image_digest": self.image_digest, "command": self.command},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


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
