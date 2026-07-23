from __future__ import annotations

from app.strategy_lab.robustness.attribution import (
    FailureAttribution,
    build_attributions,
)


def test_failure_attribution_serializes_required_fields() -> None:
    attribution = FailureAttribution(
        failure_id="failure-1",
        mechanism="trend_reversal",
        regime_dimensions=("high_volatility", "sudden_selloff"),
        affected_asset_count=3,
        cost_contribution=12.5,
        exposure_breach=True,
        evidence_ids=("artifact-1", "artifact-2"),
        extra={"note": "minimized candidate found"},
    )
    payload = attribution.to_dict()
    assert payload["failure_id"] == "failure-1"
    assert payload["mechanism"] == "trend_reversal"
    assert payload["regime_dimensions"] == ["high_volatility", "sudden_selloff"]
    assert payload["affected_asset_count"] == 3
    assert payload["cost_contribution"] == 12.5
    assert payload["exposure_breach"] is True
    assert payload["evidence_ids"] == ["artifact-1", "artifact-2"]
    assert payload["extra"] == {"note": "minimized candidate found"}


def test_build_attributions_uses_replay_artifact_id() -> None:
    failures = [
        {
            "failure_id": "failure-a",
            "category": "trend_reversal",
            "replay_artifact_id": "artifact-a",
        },
        {
            "failure_id": "failure-b",
            "category": "liquidity_deterioration",
        },
    ]
    attributions = build_attributions(
        failures,
        affected_asset_count=2,
        cost_contribution=7.25,
        exposure_breach=True,
        extra={"source": "campaign_engine"},
    )
    assert len(attributions) == 2
    assert attributions[0]["mechanism"] == "trend_reversal"
    assert attributions[0]["evidence_ids"] == ["artifact-a"]
    assert attributions[0]["affected_asset_count"] == 2
    assert attributions[1]["evidence_ids"] == []
    for item in attributions:
        assert "regime_dimensions" in item
        assert isinstance(item["regime_dimensions"], list)


def test_build_attributions_maps_category_to_mechanism() -> None:
    cases = {
        "trend_reversal": "trend_reversal",
        "volatility_expansion": "volatility_expansion",
        "correlation_breakdown": "correlation_breakdown",
        "liquidity_deterioration": "liquidity_deterioration",
        "transaction_cost_inflation": "transaction_cost_inflation",
        "parameter_instability": "parameter_instability",
        "overfitting": "overfitting",
        "unknown_category": "unknown_failure_mechanism",
    }
    for category, expected_mechanism in cases.items():
        attributions = build_attributions([{"failure_id": "x", "category": category}])
        assert attributions[0]["mechanism"] == expected_mechanism
