"""Canonical JSON and SHA-256 commitment helpers for the exchange benchmark."""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = ["canonical_json", "digest", "digest_many"]


def canonical_json(value: Any) -> str:
    """Deterministic compact JSON (sorted keys, no whitespace, ASCII)."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def digest(value: Any) -> str:
    """SHA-256 of the canonical JSON encoding of ``value``."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def digest_many(values: Any) -> str:
    """Digest an ordered collection without collapsing duplicates."""

    return digest({"items": list(values)})
