"""Fenrix evidence subsystem (reset architecture ``app/evidence/``).

Holds the artifact store, and (in later phases) the manifest, provenance,
strategy-card, and verification modules.
"""

from __future__ import annotations

from app.evidence.artifact_store import (
    ArtifactRef,
    ArtifactStore,
    FilesystemArtifactStore,
    InMemoryArtifactStore,
)

__all__ = [
    "ArtifactRef",
    "ArtifactStore",
    "FilesystemArtifactStore",
    "InMemoryArtifactStore",
]
