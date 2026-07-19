from app.decision_benchmark import build_decision_change_benchmark


def test_decision_benchmark_proves_a_stress_induced_policy_switch() -> None:
    record = build_decision_change_benchmark()

    assert record["decision_changed"] is True
    assert record["public_winner"]["policy_id"] == "aggressive_pov"
    assert record["protected_robustness_winner"]["policy_id"] == "guarded_pov"
    assert len(record["artifact_hash"]) == 64
    assert record["evidence"]["matrix_hash"]


def test_decision_benchmark_is_reproducible() -> None:
    first = build_decision_change_benchmark((42,))
    second = build_decision_change_benchmark((42,))

    assert first == second
