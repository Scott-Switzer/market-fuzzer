from __future__ import annotations

from typing import Any

from app.arena import build_challenge, evaluate_submission, public_challenge, validate_submission_csv
from app.challenges.base import ValidationResult


class ResearchChallengeEngine:
    """Adapter for the secondary CSV/positions research challenge."""

    challenge_type = "research"
    schema_version = "1.0"

    def public_brief(self, challenge_id: str) -> dict[str, Any]:
        challenge = build_challenge(challenge_id=challenge_id)
        return public_challenge(challenge)

    def validate_submission(self, submission: Any) -> ValidationResult:
        result = validate_submission_csv(str(submission), build_challenge())
        return ValidationResult(bool(result["valid"]), tuple(result.get("errors", [])))

    def run_public(self, submission: Any, seed: int = 42) -> dict[str, Any]:
        del seed
        result = evaluate_submission(build_challenge(), str(submission))
        return {"public_score": result.get("public_score"), "valid": result["valid"]}

    def run_hidden(self, submission: Any) -> dict[str, Any]:
        return evaluate_submission(build_challenge(), str(submission))

    def release_view(self, evaluation: dict[str, Any]) -> dict[str, Any]:
        return {
            "public_score": evaluation.get("public_score"),
            "robustness_score": evaluation.get("robustness_score"),
            "hidden_metrics": evaluation.get("hidden_metrics"),
        }
