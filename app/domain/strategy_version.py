"""Strategy versioning and approval contracts.

A StrategyVersion is an immutable, approved snapshot of a StrategySpec. Approval
locks the canonical hash; from that point every downstream stage must carry the
SAME hash (integrity gate 19).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.strategy_spec import StrategySpec


class ApprovalState(StrEnum):
    DRAFT = "draft"
    LOCKED = "locked"


class StrategyVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    version: int = 1
    canonical_hash: str
    spec: StrategySpec
    state: ApprovalState = ApprovalState.DRAFT
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_spec(cls, spec: StrategySpec, version: int = 1) -> StrategyVersion:
        return cls(
            strategy_id=spec.strategy_id,
            version=version,
            canonical_hash=spec.canonical_hash,
            spec=spec,
        )

    def lock(self, approved_by: str) -> StrategyVersion:
        """Return a locked copy. Execution is only permitted on locked versions."""
        # Re-verify the hash matches the spec content before locking.
        if self.canonical_hash != self.spec.compute_hash():
            raise ValueError("canonical hash does not match spec content; refusing to lock")
        reasons = self.spec.blocking_reasons()
        if reasons:
            raise ValueError(f"cannot lock a non-executable spec: {reasons}")
        return self.model_copy(
            update={
                "state": ApprovalState.LOCKED,
                "approved_by": approved_by,
                "approved_at": datetime.now(UTC),
            }
        )

    @property
    def is_locked(self) -> bool:
        return self.state == ApprovalState.LOCKED


__all__ = ["ApprovalState", "StrategyVersion"]
