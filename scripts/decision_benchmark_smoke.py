from app.decision_benchmark import build_decision_change_benchmark

record = build_decision_change_benchmark()
assert record["decision_changed"] is True
print(
    "decision benchmark: "
    f"public={record['public_winner']['policy_id']} "
    f"protected={record['protected_robustness_winner']['policy_id']} "
    f"artifact={record['artifact_hash'][:12]}"
)
