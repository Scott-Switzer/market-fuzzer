"""ArtifactStore -- abstraction over durable run artifacts.

Reset brief section 3.3 / section 11 / integrity gate 22: artifacts must live behind an
interface, addressed by opaque keys, never by leaking local filesystem paths
into public responses.

Implementations:
* :class:`FilesystemArtifactStore` -- local development (default).
* :class:`InMemoryArtifactStore` -- unit tests / fixtures.
* An S3-compatible implementation is specified for production (see
  ``docs/architecture/ARTIFACT_LIFECYCLE.md``) and intentionally not built in
  this phase; the interface makes it a drop-in.

Public callers only ever see ``ArtifactRef`` (store name + opaque key + sha256 +
size), never an absolute path. A signed/temporary URL is produced by
``public_url`` which returns a store-relative handle, not a disk path.
"""

from __future__ import annotations

import hashlib
import io
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArtifactRef:
    """Opaque, publicly-safe reference to a stored artifact."""

    store: str
    key: str  # namespace-relative key, e.g. "run/<hash>/historical/metrics.json"
    sha256: str
    size: int
    content_type: str = "application/octet-stream"

    def public_dict(self) -> dict[str, str | int]:
        """Serialization safe to return in a public API response.

        Explicitly contains NO filesystem path.
        """
        return {
            "store": self.store,
            "key": self.key,
            "sha256": self.sha256,
            "size": self.size,
            "content_type": self.content_type,
        }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ArtifactStore(ABC):
    """Content-addressable-ish artifact store keyed by caller-chosen keys."""

    name: str = "abstract"

    @abstractmethod
    def put(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> ArtifactRef: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def list(self, prefix: str = "") -> list[str]: ...

    def put_text(self, key: str, text: str, *, content_type: str = "text/plain") -> ArtifactRef:
        return self.put(key, text.encode("utf-8"), content_type=content_type)

    def put_stream(
        self, key: str, stream: io.BufferedIOBase, *, content_type: str = "application/octet-stream"
    ) -> ArtifactRef:
        return self.put(key, stream.read(), content_type=content_type)

    def public_url(self, key: str) -> str:
        """Return a store-relative handle. NEVER a local filesystem path.

        Production S3 store overrides this with a presigned URL.
        """
        if not self.exists(key):
            raise KeyError(key)
        return f"artifact://{self.name}/{key}"

    def verify(self, ref: ArtifactRef) -> bool:
        """Re-read the artifact and confirm its sha256 + size still match."""
        if not self.exists(ref.key):
            return False
        data = self.get(ref.key)
        return _sha256_bytes(data) == ref.sha256 and len(data) == ref.size


class InMemoryArtifactStore(ArtifactStore):
    name = "memory"

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
        self._ct: dict[str, str] = {}

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> ArtifactRef:
        key = _normalize_key(key)
        self._data[key] = bytes(data)
        self._ct[key] = content_type
        return ArtifactRef(
            store=self.name,
            key=key,
            sha256=_sha256_bytes(data),
            size=len(data),
            content_type=content_type,
        )

    def get(self, key: str) -> bytes:
        return self._data[_normalize_key(key)]

    def exists(self, key: str) -> bool:
        return _normalize_key(key) in self._data

    def list(self, prefix: str = "") -> list[str]:
        prefix = _normalize_key(prefix) if prefix else ""
        return sorted(k for k in self._data if k.startswith(prefix))


class FilesystemArtifactStore(ArtifactStore):
    name = "filesystem"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        key = _normalize_key(key)
        # Guard against traversal: resolved path must stay under root.
        p = (self._root / key).resolve()
        if not str(p).startswith(str(self._root)):
            raise ValueError(f"artifact key escapes store root: {key!r}")
        return p

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> ArtifactRef:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: temp then replace.
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
        return ArtifactRef(
            store=self.name,
            key=_normalize_key(key),
            sha256=_sha256_bytes(data),
            size=len(data),
            content_type=content_type,
        )

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list(self, prefix: str = "") -> list[str]:
        base = self._root
        out: list[str] = []
        for p in base.rglob("*"):
            if p.is_file() and not p.name.endswith(".tmp"):
                rel = str(p.relative_to(base))
                if not prefix or rel.startswith(_normalize_key(prefix)):
                    out.append(rel)
        return sorted(out)

    def clear(self) -> None:
        """Test helper: wipe the store root."""
        if self._root.exists():
            shutil.rmtree(self._root)
        self._root.mkdir(parents=True, exist_ok=True)


def _normalize_key(key: str) -> str:
    key = key.strip().lstrip("/")
    if ".." in Path(key).parts:
        raise ValueError(f"artifact key may not contain '..': {key!r}")
    return key


__all__ = [
    "ArtifactRef",
    "ArtifactStore",
    "InMemoryArtifactStore",
    "FilesystemArtifactStore",
]
