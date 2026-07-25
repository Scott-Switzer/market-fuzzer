from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.world.manifest_schema import (
    WorldManifest,
)
from app.world.plugin_interface import BankPlugin


@dataclass(frozen=True)
class BankSnapshot:
    manifest_id: str
    digest: str
    family_id: str
    path: Path | None = None


class SyntheticWorldBank:
    def __init__(self) -> None:
        self._manifests: dict[str, WorldManifest] = {}
        self._plugins: list[BankPlugin] = []
        self._snapshots: list[BankSnapshot] = []

    def register_plugin(self, plugin: BankPlugin) -> None:
        if plugin not in self._plugins:
            self._plugins.append(plugin)

    def admit(
        self,
        manifest: WorldManifest,
        *,
        evaluate_score: float | None = None,
        replay_digest: str | None = None,
        export_target: str | None = None,
    ) -> BankSnapshot:
        expected = manifest.recompute_digest()
        if manifest.digest != expected:
            raise ValueError("manifest digest mismatch")
        if manifest.leakage_tests is not None and not manifest.leakage_tests.passed:
            raise ValueError("manifest failed leakage checks")
        self._manifests[manifest.manifest_id] = manifest
        for plugin in self._plugins:
            plugin.on_admit(manifest)
        if evaluate_score is not None:
            for plugin in self._plugins:
                plugin.on_evaluate(manifest, evaluate_score)
        if replay_digest is not None:
            for plugin in self._plugins:
                plugin.on_replay(manifest, replay_digest)
        if export_target is not None:
            for plugin in self._plugins:
                plugin.on_export(manifest, export_target)
        snapshot = BankSnapshot(
            manifest_id=manifest.manifest_id,
            digest=manifest.digest,
            family_id=manifest.family_id,
        )
        self._snapshots.append(snapshot)
        return snapshot
