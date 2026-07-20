"""Digest-pinned, no-egress container strategy session primitives."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from typing import Any

from app.strategy_protocol import StrategyActionV1, StrategyObservationV1

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


@dataclass(frozen=True, slots=True)
class StrategyResponseRecordV1:
    idempotency_key: str
    request_digest: str
    response_digest: str
    action: dict[str, Any]


class ContainerStrategySessionV1:
    """Execute one bounded decision without importing or networking customer code."""

    def __init__(self, artifact: ContainerStrategyArtifactV1) -> None:
        self.artifact = artifact

    def decide(self, observation: dict[str, Any]) -> StrategyResponseRecordV1:
        public = StrategyObservationV1.model_validate(observation).model_dump(mode="json")
        request_digest = _digest(public)
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
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError("isolated strategy execution failed closed") from error
        if len(completed.stdout.encode()) > _MAX_MESSAGE_BYTES:
            raise RuntimeError("isolated strategy response exceeds message budget")
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        if len(lines) != 1:
            raise RuntimeError("isolated strategy must emit exactly one JSONL response")
        try:
            action = StrategyActionV1.model_validate(json.loads(lines[0])).model_dump(mode="json")
        except (json.JSONDecodeError, ValueError) as error:
            raise RuntimeError("isolated strategy emitted an invalid action") from error
        return StrategyResponseRecordV1(
            idempotency_key=_digest({"artifact": self.artifact.artifact_digest, "request": request_digest}),
            request_digest=request_digest,
            response_digest=_digest(action),
            action=action,
        )
