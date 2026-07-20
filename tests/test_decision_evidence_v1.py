from app.evaluation.decision_v1 import PairedOutcomeV1, paired_decision_evidence


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
