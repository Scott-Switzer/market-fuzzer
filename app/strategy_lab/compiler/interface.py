from __future__ import annotations

from typing import Any

from app.strategy_lab.compiler.clause_classifier import ClauseClassifier


class StrategyCompiler:
    def __init__(self) -> None:
        self.clause_classifier = ClauseClassifier()

    def compile(
        self, raw_text: str, user_resolution_overrides: dict[str, str] | None = None
    ) -> dict[str, Any]:
        clauses = self._build_clause_ledger(raw_text)
        return {
            "clauses": clauses,
            "raw_text": raw_text,
        }

    def _build_clause_ledger(self, raw_text: str) -> list[dict[str, Any]]:
        sentences = [s.strip() for s in raw_text.split(".") if s.strip()]
        clauses: list[dict[str, Any]] = []
        for idx, sentence in enumerate(sentences, start=1):
            clause = self.clause_classifier.build_clause(f"c{idx}", sentence)
            clauses.append(clause)
        return clauses
