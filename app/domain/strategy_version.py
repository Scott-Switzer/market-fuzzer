"""Strategy versioning and the immutable approved snapshot.

Reset brief Phase 1.1 item 3 (identity) and item 4 (immutable approval):

* A logical strategy has a stable ``strategy_id`` (UUID) that persists across
  versions. ``version`` is a monotonically increasing integer within that id.
* ``ApprovedStrategyVersion`` is the immutable source of truth for an approved
  version. It stores the approved ``canonical_json`` (a string) plus the hash,
  and is ``frozen=True``. The executable ``StrategySpec`` is reconstructed only
  by validating that stored canonical JSON, and its hash is re-verified every
  time it crosses an execution boundary.
* Approval never uses ``model_copy(update=...)`` (Pydantic does not validate
  update data). Approval constructs a fully-validated object via
  ``model_validate``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.strategy_spec import StrategySpec, StrategyType


def new_strategy_id() -> str:
    """Stable logical-strategy identity. UUID4 (see docs/architecture/DOMAIN_MODEL.md)."""
    return str(uuid.uuid4())


class DraftStrategy(BaseModel):
    """A mutable-by-reconstruction draft: transform only by building a new spec.

    Holds the logical identity alongside the current draft spec. The spec itself
    is frozen; to change it, validate a replacement and call ``with_spec``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str = Field(default_factory=new_strategy_id)
    version: int = Field(default=1, ge=1)
    spec: StrategySpec

    def with_spec(self, spec: StrategySpec) -> DraftStrategy:
        """Return a NEW draft carrying the same identity and a new validated spec."""
        return DraftStrategy(strategy_id=self.strategy_id, version=self.version, spec=spec)

    def next_version(self, spec: StrategySpec) -> DraftStrategy:
        """Return a NEW draft that is the next version of the SAME logical strategy."""
        return DraftStrategy(strategy_id=self.strategy_id, version=self.version + 1, spec=spec)

    @property
    def canonical_hash(self) -> str:
        return self.spec.compute_hash()

    def approve(
        self,
        approved_by: str,
        *,
        supported_types: set[StrategyType] | None = None,
        available_symbols: set[str] | None = None,
    ) -> ApprovedStrategyVersion:
        """Validate executability, then build an IMMUTABLE approved snapshot.

        Uses ``model_validate`` (fully validated), never ``model_copy(update=)``.
        """
        reasons = self.spec.blocking_reasons(
            supported_types=supported_types, available_symbols=available_symbols
        )
        if reasons:
            raise ValueError(f"cannot approve a non-executable spec: {reasons}")
        return ApprovedStrategyVersion.model_validate(
            {
                "strategy_id": self.strategy_id,
                "version": self.version,
                "schema_version": self.spec.schema_version,
                "canonical_hash": self.spec.compute_hash(),
                "canonical_json": self.spec.canonical_json(),
                "approved_by": approved_by,
                "approved_at": datetime.now(UTC),
            }
        )


class ApprovedStrategyVersion(BaseModel):
    """Immutable approved snapshot. The canonical JSON is the source of truth."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str
    version: int = Field(ge=1)
    schema_version: str
    canonical_hash: str
    canonical_json: str
    approved_by: str
    approved_at: datetime

    def to_spec(self) -> StrategySpec:
        """Reconstruct the executable spec by VALIDATING the stored canonical JSON.

        Re-verifies the hash: a tampered ``canonical_json`` or ``canonical_hash``
        raises. Call this at every execution boundary.
        """
        spec = StrategySpec.model_validate_json(self.canonical_json)
        # The stored canonical_json omits volatile keys (id/version/compiler_meta),
        # so the reconstructed spec carries a fresh random strategy_id; re-stamp
        # the logical identity from the approved envelope before returning.
        spec = StrategySpec.model_validate(
            {
                **spec.model_dump(mode="python"),
                "strategy_id": self.strategy_id,
                "strategy_version": self.version,
            }
        )
        computed = spec.compute_hash()
        if computed != self.canonical_hash:
            raise ValueError(
                f"approved canonical hash mismatch: stored={self.canonical_hash} computed={computed} "
                "(canonical_json or canonical_hash was tampered)"
            )
        return spec

    def verify(self) -> bool:
        """True iff the stored canonical JSON still hashes to the stored hash."""
        try:
            spec = StrategySpec.model_validate_json(self.canonical_json)
        except Exception:
            return False
        return spec.compute_hash() == self.canonical_hash


__all__ = ["new_strategy_id", "DraftStrategy", "ApprovedStrategyVersion"]
