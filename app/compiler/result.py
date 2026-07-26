"""Typed compilation result (reset brief Phase 2 item 25).

The compiler NEVER silently swaps strategy families. Unknown/unsupported prose
yields ``strategy_type = unsupported`` with unsupported clauses and execution
blocked -- not a different strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.strategy_spec import StrategySpec

COMPILER_VERSION = "deterministic/1.0"


@dataclass(frozen=True)
class LedgerEntry:
    """One interpreted semantic clause of the thesis. Never dropped."""

    id: str
    original_text_span: str
    normalized_interpretation: str
    status: str  # resolved | assumption | unsupported | needs_resolution
    confidence: float
    assumption: str | None = None
    resolution: str | None = None


@dataclass(frozen=True)
class CompilationResult:
    original_thesis: str
    strategy_spec_draft: StrategySpec
    clauses: list[LedgerEntry] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    unsupported_clauses: list[LedgerEntry] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    required_data: list[str] = field(default_factory=list)
    required_user_resolutions: list[str] = field(default_factory=list)
    compiler_kind: str = "deterministic"
    compiler_version: str = COMPILER_VERSION
    confidence_by_clause: dict[str, float] = field(default_factory=dict)

    @property
    def is_supported(self) -> bool:
        return not self.unsupported_clauses and self.strategy_spec_draft.strategy_type.value != "unsupported"


__all__ = ["CompilationResult", "LedgerEntry", "COMPILER_VERSION"]
