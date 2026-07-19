from app.decision_benchmark import build_decision_change_benchmark

record = build_decision_change_benchmark()
if record["decision_changed"] is not True:
    raise AssertionError(
        f"Expected a policy switch but got: public={record['public_winner']['policy_id']} "
        f"protected={record['protected_robustness_winner']['policy_id']}"
    )
print(
    "decision benchmark: "
    f"public={record['public_winner']['policy_id']} "
    f"protected={record['protected_robustness_winner']['policy_id']} "
    f"artifact={record['artifact_hash'][:12]}"
)
