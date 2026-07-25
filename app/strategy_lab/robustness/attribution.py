from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FailureAttribution:
    failure_id: str
    mechanism: str
    regime_dimensions: tuple[str, ...] = field(default_factory=tuple)
    affected_asset_count: int = 0
    cost_contribution: float = 0.0
    exposure_breach: bool = False
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_id": self.failure_id,
            "mechanism": self.mechanism,
            "regime_dimensions": list(self.regime_dimensions),
            "affected_asset_count": self.affected_asset_count,
            "cost_contribution": self.cost_contribution,
            "exposure_breach": self.exposure_breach,
            "evidence_ids": list(self.evidence_ids),
            "extra": self.extra,
        }


def _regime_label_for_failure(failure: dict[str, Any]) -> tuple[str, ...]:
    return ("synthetic_stress",)


def _mechanism_for_category(category: str) -> str:
    category = str(category or "").lower()
    if "reversal" in category:
        return "trend_reversal"
    if "volatility" in category:
        return "volatility_expansion"
    if "correlation" in category:
        return "correlation_breakdown"
    if "liquidity" in category:
        return "liquidity_deterioration"
    if "transaction" in category or "cost" in category:
        return "transaction_cost_inflation"
    if "parameter" in category:
        return "parameter_instability"
    if "overfit" in category:
        return "overfitting"
    return "unknown_failure_mechanism"


def build_attributions(
    failures: list[dict[str, Any]],
    *,
    affected_asset_count: int = 1,
    cost_contribution: float = 0.0,
    exposure_breach: bool = False,
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    attributions = []
    for failure in failures:
        failure_id = str(failure.get("failure_id") or "")
        category = str(failure.get("category") or "unknown")
        regime_dimensions = _regime_label_for_failure(failure)
        mechanism = _mechanism_for_category(category)
        evidence_ids: tuple[str, ...] = ()
        replay_artifact_id = failure.get("replay_artifact_id")
        if replay_artifact_id:
            evidence_ids = (str(replay_artifact_id),)
        attribution = FailureAttribution(
            failure_id=failure_id,
            mechanism=mechanism,
            regime_dimensions=regime_dimensions,
            affected_asset_count=affected_asset_count,
            cost_contribution=float(cost_contribution),
            exposure_breach=bool(exposure_breach),
            evidence_ids=evidence_ids,
            extra=dict(extra or {}),
        )
        attributions.append(attribution.to_dict())
    return attributions
