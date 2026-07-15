"""Exact evidence labels used to prevent structural/emergent claim drift."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

STRUCTURAL_LABEL = "structural"
EMERGENT_LABEL = "emergent"


class EvidenceNature(StrEnum):
    """Whether a property is enforced by code or measured in simulated output."""

    STRUCTURAL = STRUCTURAL_LABEL
    EMERGENT = EMERGENT_LABEL


class LabeledFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: EvidenceNature
    statement: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)


def assert_exact_evidence_label(label: str | EvidenceNature) -> EvidenceNature:
    """Return the canonical label or reject synonyms that could blur claims."""

    try:
        return EvidenceNature(label)
    except ValueError as exc:
        raise ValueError(
            f"evidence label must be exactly {STRUCTURAL_LABEL!r} or {EMERGENT_LABEL!r}"
        ) from exc


def label_structural_property(statement: str, evidence: list[str] | None = None) -> LabeledFinding:
    """Label a property enforced by schemas, accounting, or matching code."""

    return LabeledFinding(
        label=EvidenceNature.STRUCTURAL,
        statement=statement,
        evidence=evidence or [],
    )


def label_emergent_result(statement: str, evidence: list[str] | None = None) -> LabeledFinding:
    """Label a measured outcome that may vary by specification and seed."""

    return LabeledFinding(
        label=EvidenceNature.EMERGENT,
        statement=statement,
        evidence=evidence or [],
    )
