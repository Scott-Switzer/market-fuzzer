"""Canonical evidence envelopes shared by development, primary, and diagnostic workflows.

The envelope is intentionally an evidence contract rather than a scoring engine.
It prevents a deterministic fixture or adaptive failure search from being silently
presented as an independently selected sealed primary evaluation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from .sealed_v1 import PreparedCampaignV1, SealedEvaluationError


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _digest_is_valid(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())


class EvidenceScopeV1(StrEnum):
    DEVELOPMENT = "development_fixture"
    SEALED_PRIMARY = "sealed_primary"
    ADAPTIVE_DIAGNOSTIC = "adaptive_diagnostic"


@dataclass(frozen=True, slots=True)
class EvaluationEvidenceV1:
    """Portable, canonical claim boundary for a workflow result."""

    scope: EvidenceScopeV1
    strategy_artifact_digest: str | None
    campaign_commitment_digest: str | None
    result_digest: str
    claim_boundary: str
    limitations: tuple[str, ...]
    mechanism: str | None = None
    parent_primary_result_digest: str | None = None
    schema_version: str = "evaluation-evidence-v1"

    def __post_init__(self) -> None:
        if not _digest_is_valid(self.result_digest) or not self.claim_boundary or not self.limitations:
            raise SealedEvaluationError("evidence requires a result digest, claim boundary, and limitations")
        if self.scope == EvidenceScopeV1.SEALED_PRIMARY:
            if not _digest_is_valid(self.strategy_artifact_digest or "") or not _digest_is_valid(
                self.campaign_commitment_digest or ""
            ):
                raise SealedEvaluationError(
                    "sealed primary evidence requires frozen artifact and commitment digests"
                )
            if self.mechanism is not None or self.parent_primary_result_digest is not None:
                raise SealedEvaluationError("primary evidence cannot carry adaptive diagnostic fields")
        elif self.scope == EvidenceScopeV1.ADAPTIVE_DIAGNOSTIC:
            if not self.mechanism:
                raise SealedEvaluationError("adaptive diagnostics require a failure mechanism")
            if self.campaign_commitment_digest is not None:
                raise SealedEvaluationError("adaptive diagnostics cannot claim a primary campaign commitment")
            if self.parent_primary_result_digest is not None and not _digest_is_valid(
                self.parent_primary_result_digest
            ):
                raise SealedEvaluationError("adaptive diagnostic parent result digest is invalid")
        elif (
            self.strategy_artifact_digest is not None
            or self.campaign_commitment_digest is not None
            or self.mechanism is not None
            or self.parent_primary_result_digest is not None
        ):
            raise SealedEvaluationError("development fixtures cannot claim sealed or adaptive provenance")

    @property
    def evidence_digest(self) -> str:
        return _canonical_digest(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["scope"] = self.scope.value
        value["evidence_digest"] = self.evidence_digest
        return value


def development_fixture_evidence(*, payload: object, limitation: str) -> EvaluationEvidenceV1:
    """Label public, deterministic fixtures honestly; they are never sealed primary rankings."""
    return EvaluationEvidenceV1(
        scope=EvidenceScopeV1.DEVELOPMENT,
        strategy_artifact_digest=None,
        campaign_commitment_digest=None,
        result_digest=_canonical_digest(payload),
        claim_boundary="Deterministic development fixture inside declared synthetic mechanisms; not sealed primary evaluation.",
        limitations=(limitation,),
    )


def sealed_primary_evidence(campaign: PreparedCampaignV1) -> EvaluationEvidenceV1:
    """Convert only a finalized M5 primary result into sealed-primary evidence."""
    result = campaign.finalized_primary_result
    if result is None:
        raise SealedEvaluationError("sealed primary evidence requires a finalized campaign")
    return EvaluationEvidenceV1(
        scope=EvidenceScopeV1.SEALED_PRIMARY,
        strategy_artifact_digest=result.strategy_artifact_digest,
        campaign_commitment_digest=result.campaign_commitment_digest,
        result_digest=result.result_digest,
        claim_boundary="Sealed primary evidence from an artifact frozen before hidden world generation.",
        limitations=(
            "This evidence is bounded to the committed generator bundle, campaign policy, and finite world sample.",
        ),
    )


def adaptive_diagnostic_evidence(
    *, payload: object, mechanism: str, limitation: str, parent_primary_result_digest: str | None = None
) -> EvaluationEvidenceV1:
    """Record strategy-aware failure discovery without allowing it to affect primary ranking."""
    return EvaluationEvidenceV1(
        scope=EvidenceScopeV1.ADAPTIVE_DIAGNOSTIC,
        strategy_artifact_digest=None,
        campaign_commitment_digest=None,
        result_digest=_canonical_digest(payload),
        claim_boundary="Adaptive, strategy-aware diagnostic evidence; it is not an independently selected primary score.",
        limitations=(limitation,),
        mechanism=mechanism,
        parent_primary_result_digest=parent_primary_result_digest,
    )
