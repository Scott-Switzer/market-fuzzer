from app.analytics.claims import evaluate_participation_claim, spearman


def fixture_rows(costs_by_set: dict[str, list[float]]) -> list[dict]:
    rates = [0.02, 0.05, 0.10, 0.20]
    rows = []
    for set_id, costs in costs_by_set.items():
        for seed in range(8):
            for rate, cost in zip(rates, costs, strict=True):
                rows.append(
                    {
                        "calibration_parameter_set_id": set_id,
                        "seed": seed,
                        "participation_rate": rate,
                        "implementation_shortfall_bps": cost + seed * 0.01,
                    }
                )
    return rows


def test_exact_claim_gate_passes_directional_fixture():
    result = evaluate_participation_claim(
        fixture_rows({"a": [1, 2, 4, 8], "b": [1, 3, 5, 9], "c": [2, 3, 6, 10]})
    )
    assert result.permitted
    assert result.spearman_rho >= 0.70
    assert result.positive_paired_change_fraction >= 0.70
    assert result.bootstrap_slope_interval[0] > 0
    assert result.calibration_set_agreement >= 0.80


def test_claim_gate_blocks_contradictory_and_calibration_unstable_fixture():
    result = evaluate_participation_claim(
        fixture_rows({"a": [1, 4, 2, 1], "b": [1, 3, 2, 0], "c": [1, 2, 1, -1]})
    )
    assert not result.permitted
    assert result.blocking_reasons
    assert result.uncertainty_diagnosis


def test_spearman_handles_ties():
    assert spearman([1, 2, 3, 4], [1, 2, 2, 4]) > 0.9
