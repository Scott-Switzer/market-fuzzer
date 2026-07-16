from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()


class ChallengeEngine(Protocol):
    """Common boundary shared by challenge products.

    Implementations own validation and evaluation, while API code owns identity,
    persistence, lifecycle, and release authorization.
    """

    challenge_type: str
    schema_version: str

    def public_brief(self, challenge_id: str) -> dict[str, Any]: ...

    def validate_submission(self, submission: Any) -> ValidationResult: ...

    def run_public(self, submission: Any, seed: int = 42) -> dict[str, Any]: ...

    def run_hidden(self, submission: Any) -> dict[str, Any]: ...

    def release_view(self, evaluation: dict[str, Any]) -> dict[str, Any]: ...
