from __future__ import annotations

from typing import Any


class EvidenceLinkedSuggestionEngine:
    @staticmethod
    def suggest(failure: dict[str, Any]) -> list[dict[str, Any]]:
        suggestions = []
        category = failure.get("category", "")

        if category == "excessive_turnover" or "turnover" in str(failure):
            suggestions.append(
                {
                    "change_category": "add_turnover_cap",
                    "expected_tradeoff": "lower turnover, potentially smoother returns",
                    "test_recommendation": "rerun sealed campaign with 0.25x turnover cap",
                    "evidence_link": "High turnover observed in failure scenario.",
                }
            )

        if category == "drawdown_breach" or "drawdown" in str(failure):
            suggestions.append(
                {
                    "change_category": "add_stop_loss",
                    "expected_tradeoff": "reduced max drawdown, possible whip-saw risk",
                    "test_recommendation": "add strict daily stop loss and re-evaluate",
                    "evidence_link": "Drawdown breached threshold.",
                }
            )

        if not suggestions:
            suggestions.append(
                {
                    "change_category": "parameter_tuning",
                    "expected_tradeoff": "different performance characteristics",
                    "test_recommendation": "re-evaluate with broader parameter sweep",
                    "evidence_link": "General failure observed.",
                }
            )

        return suggestions
