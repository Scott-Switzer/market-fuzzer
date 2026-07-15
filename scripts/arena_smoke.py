"""Offline smoke test for the Quant Challenge Arena teaching fixture."""

from app.arena import build_challenge, evaluate_submission, example_submission


def main() -> None:
    challenge = build_challenge()
    public_winner = evaluate_submission(challenge, example_submission(challenge, "backtest_winner"))
    robust_winner = evaluate_submission(challenge, example_submission(challenge, "robust_generalizer"))
    assert public_winner["public_score"] > robust_winner["public_score"]
    assert robust_winner["robustness_score"] > public_winner["robustness_score"]
    assert public_winner["hidden_metrics"]["hidden_sharpe"] < 0
    assert robust_winner["hidden_metrics"]["hidden_sharpe"] > 0
    print(
        "arena smoke: public winner=",
        public_winner["public_score"],
        "robust winner=",
        robust_winner["robustness_score"],
        "status=pass",
    )


if __name__ == "__main__":
    main()
