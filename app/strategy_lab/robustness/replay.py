from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReplayArtifact:
    artifact_id: str
    campaign_id: str | None
    evaluation_index: int | None
    strategy_type: str
    parameters: dict[str, Any]
    world: dict[str, Any]
    prices: list[float]
    positions: list[float]
    metrics: dict[str, Any]
    failure_record: dict[str, Any] | None = None
    created_at: str = ""
    checksum: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "schema_version": "replay_artifact_v1",
            "campaign_id": self.campaign_id,
            "evaluation_index": self.evaluation_index,
            "strategy_type": self.strategy_type,
            "parameters": self.parameters,
            "world": self.world,
            "prices": self.prices,
            "positions": self.positions,
            "metrics": self.metrics,
            "failure_record": self.failure_record,
            "created_at": self.created_at,
            "checksum": self.checksum,
        }


class ReplayStore:
    def __init__(self, root: str | None = None) -> None:
        self.root = (
            Path(root)
            if root is not None
            else Path(os.environ.get("REPLAY_ARTIFACT_ROOT", "artifacts/replays"))
        )

    def store(self, event_trace: list[dict[str, Any]], *, name: str | None = None) -> dict[str, Any]:
        return {"events": event_trace, "status": "artifact", "artifact_id": ""}


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _checksum(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def build_artifact(
    *,
    campaign_id: str | None,
    evaluation_index: int | None,
    strategy_type: str,
    parameters: dict[str, Any],
    world: dict[str, Any],
    prices: list[float],
    positions: list[float],
    metrics: dict[str, Any],
    failure_record: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> ReplayArtifact:
    created = created_at or __import__("datetime").datetime.now(UTC).isoformat()
    contract = {
        "strategy_type": strategy_type,
        "parameters": parameters,
        "world": world,
        "prices": prices,
        "positions": positions,
        "metrics": metrics,
    }
    checksum = _checksum(contract)
    return ReplayArtifact(
        artifact_id=hashlib.sha256(_canonical_json(contract).encode()).hexdigest()[:18],
        campaign_id=campaign_id,
        evaluation_index=evaluation_index,
        strategy_type=strategy_type,
        parameters=parameters,
        world=world,
        prices=prices,
        positions=positions,
        metrics=metrics,
        failure_record=failure_record,
        created_at=created,
        checksum=checksum,
    )


def store_artifact(root: str | None, artifact: ReplayArtifact) -> dict[str, Any]:
    path = (
        Path(root) if root is not None else Path(os.environ.get("REPLAY_ARTIFACT_ROOT", "artifacts/replays"))
    )
    path.mkdir(parents=True, exist_ok=True)
    idx = artifact.evaluation_index if artifact.evaluation_index is not None else "index"
    filename = f"{idx:08d}_{artifact.artifact_id}.replay.json"
    target = path / filename
    payload = artifact.to_json()
    canonical = _canonical_json(payload)
    target.write_text(canonical, encoding="utf-8")
    return {"path": str(target), "artifact_id": artifact.artifact_id, "status": "stored"}


def load_artifact(root: str | None, artifact_id: str) -> dict[str, Any]:
    path = (
        Path(root) if root is not None else Path(os.environ.get("REPLAY_ARTIFACT_ROOT", "artifacts/replays"))
    )
    matches = sorted(path.glob(f"*_{artifact_id}.replay.json"))
    if not matches:
        return {"artifact_id": artifact_id, "status": "missing"}
    content = json.loads(matches[-1].read_text(encoding="utf-8"))
    return {"artifact_id": artifact_id, "artifact": content, "status": "loaded"}
