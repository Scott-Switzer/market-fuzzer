"""Fenrix canonical domain contracts.

This package holds the authoritative, versioned domain objects that every later
stage of the product (compiler, strategy registry, historical engine, synthetic
stress, exchange replay, evidence) must consume. The single most important
object is :class:`app.domain.strategy_spec.StrategySpec` -- the one canonical
strategy contract with a deterministic hash.
"""

from __future__ import annotations

__all__ = ["strategy_spec"]
