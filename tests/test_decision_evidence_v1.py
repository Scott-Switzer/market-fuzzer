from app.evaluation.decision_v1 import (
    PairedOutcomeV1,
    benjamini_hochberg_adjust,
    paired_decision_evidence,
)
from app.evaluation.sealed_v1 import (
    PrimaryEvaluationResultV1,
    PrimaryWorldMetricV1,
    PrimaryWorldResultV1,
)


def _outcomes() -> list[PairedOutcomeV1]:
    return [
        PairedOutcomeV1(f"b{index}", "agents" if index % 2 else "point_process", 10 + index, index)
        for index in range(8)
    ]


def test_paired_evidence_is_deterministic_and_uses_family_sensitivity() -> None:
    first = paired_decision_evidence("net_pnl", _outcomes(), bootstrap_draws=200, bootstrap_seed=4)
    assert first == paired_decision_evidence("net_pnl", _outcomes(), bootstrap_draws=200, bootstrap_seed=4)
    assert first.verdict == "evidence_of_difference"
    assert first.confidence_interval is not None and first.confidence_interval[0] > 0
    assert {family for family, _ in first.family_effects} == {"agents", "point_process"}


def test_paired_evidence_refuses_small_samples_and_duplicate_blocks() -> None:
    small = paired_decision_evidence("net_pnl", _outcomes()[:3], bootstrap_draws=100)
    assert small.verdict == "insufficient_evidence" and small.effect_size is None
    duplicate = _outcomes() + [PairedOutcomeV1("b0", "agents", 2, 1)]
    try:
        paired_decision_evidence("net_pnl", duplicate, bootstrap_draws=100)
    except ValueError as error:
        assert "unique block" in str(error)
    else:
        raise AssertionError("duplicate paired blocks must fail")


def test_sealed_metric_comparison_pairs_only_matching_opaque_receipts() -> None:
    worlds = tuple(PrimaryWorldResultV1(f"{index:064x}", 1, "a" * 64) for index in range(8))
    candidate = PrimaryEvaluationResultV1(
        "c" * 64,
        "a" * 64,
        worlds,
        tuple(PrimaryWorldMetricV1(world.world_receipt, "cost", 10.0) for world in worlds),
    )
    baseline = PrimaryEvaluationResultV1(
        "c" * 64,
        "b" * 64,
        worlds,
        tuple(PrimaryWorldMetricV1(world.world_receipt, "cost", 1.0) for world in worlds),
    )
    from app.evaluation.decision_v1 import sealed_metric_decision_evidence

    assert sealed_metric_decision_evidence("cost", candidate, baseline, bootstrap_draws=100).sample_size == 8


def test_bh_adjustment_does_not_promote_insufficient_evidence() -> None:
    strong = paired_decision_evidence("strong", _outcomes(), bootstrap_draws=100)
    weak = paired_decision_evidence("weak", _outcomes()[:3], bootstrap_draws=100)
    adjusted = {item.metric_name: item for item in benjamini_hochberg_adjust([strong, weak])}
    assert adjusted["weak"].discovery_supported is False
    assert adjusted["weak"].adjusted_p_value == 1.0
