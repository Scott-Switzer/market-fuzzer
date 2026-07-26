"""Canonicalization helpers for the compiler (reset brief item 14).

The canonical hash of a compiled strategy is delegated to the domain
``StrategySpec.canonical_hash`` (single source of truth). This module exposes a
thin wrapper so the compiler package has a stable name for it.
"""

from __future__ import annotations

from app.domain.strategy_spec import StrategySpec


def canonical_hash(spec: StrategySpec) -> str:
    return spec.compute_hash()


def canonical_json(spec: StrategySpec) -> str:
    return spec.canonical_json()


__all__ = ["canonical_hash", "canonical_json"]
