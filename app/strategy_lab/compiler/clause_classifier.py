from __future__ import annotations

from typing import Any

from app.strategy_lab.dsl import (
    ClauseStatus,
)


class ClauseClassifier:
    _UNSUPPORTED_KEYWORDS = (
        "news",
        "twitter",
        "sentiment",
        "insider",
        "crypto",
        "options",
        "futures",
        "earnings call",
        "buyback",
        "merger",
        "acquisition",
        "SEC",
        "10-k",
        "10-q",
        "8-k",
    )
    _AMBIGUOUS_KEYWORDS = (
        "too fast",
        "rising too fast",
        "cheapest",
        "best",
        "sketchy",
        "good",
        "bad",
        "strong",
        "weak",
        "normal",
        "reasonable",
        "appropriate",
        "suitable",
    )
    _UNSAFE_KEYWORDS = (
        "all-in",
        "all in leverage",
        "unlimited leverage",
        "short without borrow",
        "naked short",
        "margin call",
    )

    @classmethod
    def classify(cls, text: str) -> dict[str, Any]:
        lowered = text.lower()
        reason = None
        status = ClauseStatus.SUPPORTED_AND_COMPILED
        confidence = 0.9
        if any(keyword in lowered for keyword in cls._UNSAFE_KEYWORDS):
            status = ClauseStatus.REJECTED_UNSAFE_OR_INVALID
            confidence = 0.99
            reason = "Clause contains unsafe or invalid trading directives."
        elif any(keyword in lowered for keyword in cls._UNSUPPORTED_KEYWORDS):
            status = ClauseStatus.UNSUPPORTED_SAVED_FOR_RESEARCH
            confidence = 0.5
            reason = "Clause references data or instruments outside MVP scope."
        elif any(keyword in lowered for keyword in cls._AMBIGUOUS_KEYWORDS):
            status = ClauseStatus.AMBIGUOUS_REQUIRES_RESOLUTION
            confidence = 0.6
            reason = "Clause requires explicit threshold or data-source resolution."
        return {
            "status": status.value,
            "reason": reason,
            "confidence": confidence,
        }

    @classmethod
    def build_clause(cls, clause_id: str, original_text: str) -> dict[str, Any]:
        classification = cls.classify(original_text)
        return {
            "clause_id": clause_id,
            "original_text": original_text,
            "normalized_text": original_text.strip(),
            "status": classification["status"],
            "reason": classification.get("reason"),
            "compiler_confidence": classification.get("confidence"),
        }
