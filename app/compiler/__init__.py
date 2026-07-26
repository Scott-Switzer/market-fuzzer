"""Deterministic plain-English strategy compiler (reset brief Phase 2 items 25-27).

Public surface:
  compile_thesis(thesis) -> CompilationResult   (parse plain English)
  apply_resolutions(result, {...}) -> CompilationResult
  cross_sectional_to_spec(legacy) -> StrategySpec  (legacy parity bridge)

Also re-exports the counterfactual *world* compiler (compile_world/offline/gpt)
which previously lived in the ``app/compiler.py`` module, preserving imports.
"""

from __future__ import annotations

from app.compiler.canonicalizer import canonical_hash, canonical_json
from app.compiler.deterministic_parser import compile_thesis
from app.compiler.legacy_adapter import cross_sectional_to_spec
from app.compiler.resolver import apply_resolutions
from app.compiler.result import COMPILER_VERSION, CompilationResult, LedgerEntry
from app.compiler.world import compile_gpt, compile_offline, compile_world

__all__ = [
    "compile_thesis",
    "apply_resolutions",
    "cross_sectional_to_spec",
    "canonical_hash",
    "canonical_json",
    "CompilationResult",
    "LedgerEntry",
    "COMPILER_VERSION",
    "compile_world",
    "compile_offline",
    "compile_gpt",
]
