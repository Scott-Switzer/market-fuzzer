from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.challenges.base import ValidationResult
from app.execution_arena import (
    CHALLENGE_ID,
    ExecutionPolicySubmission,
    benchmark_matrix,
    challenge_overview,
    run_policy_submission,
)


class ExecutionChallengeEngine:
    challenge_type = "execution"
    schema_version = "1.0"

    def public_brief(self, challenge_id: str) -> dict[str, Any]:
        if challenge_id != CHALLENGE_ID:
            raise KeyError(challenge_id)
        return challenge_overview()

    def validate_submission(self, submission: Any) -> ValidationResult:
        try:
            ExecutionPolicySubmission.model_validate(submission)
        except ValidationError as exc:
            return ValidationResult(False, tuple(error["msg"] for error in exc.errors()))
        return ValidationResult(True)

    def run_public(self, submission: Any, seed: int = 42) -> dict[str, Any]:
        policy = ExecutionPolicySubmission.model_validate(submission)
        return run_policy_submission(policy, "engine-validation", seed)

    def run_hidden(self, submission: Any) -> dict[str, Any]:
        policy = ExecutionPolicySubmission.model_validate(submission)
        return benchmark_matrix(student_submissions={"engine-validation": policy})

    def release_view(self, evaluation: dict[str, Any]) -> dict[str, Any]:
        fields = ("policy_id", "name", "public_rank", "robustness_rank", "robustness_score")
        return {
            "rows": [{key: row[key] for key in fields if key in row} for row in evaluation["rows"]],
            "matrix_hash": evaluation["provenance"]["matrix_hash"],
        }
