"""Compilation service: plain English -> typed CompilationResult -> v2 contract.

Authoritative compiler path. Uses ONLY ``app.compiler.compile_thesis``. Never
invokes the legacy planner. Never catches a compile failure to fall back to a
different family. Unsupported prose returns an honest unsupported result.
"""

from __future__ import annotations

from app.compiler import COMPILER_VERSION, apply_resolutions, compile_thesis
from app.compiler.result import CompilationResult, LedgerEntry
from app.domain.strategy_spec import StrategySpec
from app.strategy_lab.canonical.contracts import ClauseEntry, CompileResponse, ResolveResponse
from app.strategy_lab.canonical.errors import UnresolvedClauseError

_API_VERSION = "v2"


def _clause_to_contract(c: LedgerEntry) -> ClauseEntry:
    return ClauseEntry(
        id=c.id,
        original_text_span=c.original_text_span,
        normalized_interpretation=c.normalized_interpretation,
        status=c.status,
        confidence=c.confidence,
        assumption=c.assumption,
        resolution=c.resolution,
    )


def _blocking_reasons(result: CompilationResult) -> list[str]:
    reasons: list[str] = []
    if not result.is_supported:
        reasons.append("compiled strategy type is unsupported (no executor)")
    if result.required_user_resolutions:
        reasons.extend(f"resolution required: {r}" for r in result.required_user_resolutions)
    return reasons


def compile_text(description: str) -> CompileResponse:
    """Compile plain English into the authoritative typed result."""
    result = compile_thesis(description)
    spec = result.strategy_spec_draft
    blocking = _blocking_reasons(result)
    return CompileResponse(
        api_version=_API_VERSION,
        original_thesis=result.original_thesis,
        strategy_type=spec.strategy_type.value,
        spec_draft=spec.model_dump(mode="python"),
        canonical_hash=spec.compute_hash(),
        clauses=[_clause_to_contract(c) for c in result.clauses],
        assumptions=result.assumptions,
        required_user_resolutions=result.required_user_resolutions,
        unsupported_clauses=[_clause_to_contract(c) for c in result.unsupported_clauses],
        required_data=result.required_data,
        contradictions=result.contradictions,
        compiler_kind=result.compiler_kind,
        compiler_version=result.compiler_version,
        confidence_by_clause=result.confidence_by_clause,
        is_supported=result.is_supported,
        is_approvable=result.is_supported and not blocking,
        blocking_reasons=blocking,
    )


def resolve(spec_draft: StrategySpec, resolutions: dict) -> ResolveResponse:
    """Apply explicit resolutions and re-validate deterministically.

    Resolution keys may be supplied either as the canonical short key
    (``universe``, ``fallback``) or as the full ``required_user_resolutions``
    string (``"universe: provide ..."``); the leading token before ``:`` is the
    canonical key. Unknown keys raise so callers cannot silently no-op.
    """
    if not resolutions:
        raise UnresolvedClauseError("no resolutions supplied")
    known = {"universe", "fallback"}
    normalized: dict[str, object] = {}
    for raw_key, value in resolutions.items():
        key = raw_key.split(":", 1)[0].strip()
        if key not in known:
            raise UnresolvedClauseError(f"unknown resolution key: {raw_key}")
        normalized[key] = value
    result = apply_resolutions(
        CompilationResult(
            original_thesis=spec_draft.original_thesis,
            strategy_spec_draft=spec_draft,
        ),
        normalized,
    )
    spec = result.strategy_spec_draft
    blocking = _blocking_reasons(result)
    return ResolveResponse(
        api_version=_API_VERSION,
        strategy_type=spec.strategy_type.value,
        spec_draft=spec.model_dump(mode="python"),
        canonical_hash=spec.compute_hash(),
        is_supported=result.is_supported,
        is_approvable=result.is_supported and not blocking,
        blocking_reasons=blocking,
    )


__all__ = ["compile_text", "resolve", "COMPILER_VERSION"]
