from __future__ import annotations

from typing import Protocol

from app.world.manifest_schema import WorldManifest


class BankPlugin(Protocol):
    def on_admit(self, manifest: WorldManifest) -> None:
        """Called immediately after a manifest is admitted to the bank."""

    def on_evaluate(self, manifest: WorldManifest, score: float) -> None:
        """Called after evaluation runs against the manifest."""

    def on_replay(self, manifest: WorldManifest, replayed_digest: str) -> None:
        """Called when a manifest is replayed for COSIGN verification."""

    def on_export(self, manifest: WorldManifest, target: str) -> None:
        """Called when a manifest is exported to a backup target."""
