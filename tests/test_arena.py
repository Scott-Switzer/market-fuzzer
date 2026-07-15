from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.app import app
from app.arena import (
    ChallengeGeneration,
    HiddenScenarioSpec,
    StrategyFeedback,
    build_challenge,
    deterministic_feedback,
    deterministic_generation,
    evaluate_submission,
    example_submission,
    generate_challenge_content,
    generate_dataset,
    generate_feedback,
    public_challenge,
    public_dataset,
    validate_submission_csv,
)


def test_dataset_is_deterministic_and_hidden_rows_are_separate() -> None:
    challenge = build_challenge(dataset_seed=123)
    public_one = public_dataset(challenge)
    public_two = public_dataset(challenge)
    assert public_one == public_two
    assert len(public_one["rows"]) == 24
    assert all("regime" not in row and "latent_regime" not in row for row in public_one["rows"])
    hidden = generate_dataset(challenge, include_hidden=True)
    assert len(hidden) == 48
    assert any(row["date"] > challenge.public_end.isoformat() for row in hidden)
    assert all("latent_regime" in row for row in hidden)


def test_public_challenge_excludes_hidden_manifest_and_generation_details() -> None:
    value = public_challenge(build_challenge())
    assert "hidden_regime_manifest" not in value
    assert "generation" not in value
    assert value["hidden_period"]["results_released_by_instructor"] is True


def test_submission_contract_rejects_hidden_dates_duplicates_and_exposure() -> None:
    challenge = build_challenge()
    valid = example_submission(challenge, "robust_generalizer")
    hidden = valid + f"{challenge.hidden_start.isoformat()},ASSET_01,0.1\n"
    result = validate_submission_csv(hidden, challenge)
    assert not result["valid"]
    assert any("hidden-period" in error for error in result["errors"])
    duplicate = valid + f"{challenge.public_start.isoformat()},ASSET_01,0.1\n"
    duplicate_result = validate_submission_csv(duplicate, challenge)
    assert any("duplicate" in error for error in duplicate_result["errors"])
    too_much = "date,asset,position\n" + "\n".join(
        f"{current.isoformat()},{asset},1.0"
        for current in challenge.public_dates()
        for asset in challenge.assets
    )
    exposure_result = validate_submission_csv(too_much, challenge)
    assert any("gross exposure" in error for error in exposure_result["errors"])


def test_example_ranking_reverses_between_public_and_hidden_robustness() -> None:
    challenge = build_challenge()
    winner = evaluate_submission(challenge, example_submission(challenge, "backtest_winner"))
    robust = evaluate_submission(challenge, example_submission(challenge, "robust_generalizer"))
    assert winner["public_score"] > robust["public_score"]
    assert robust["robustness_score"] > winner["robustness_score"]
    assert winner["hidden_metrics"]["hidden_sharpe"] < 0
    assert robust["hidden_metrics"]["hidden_sharpe"] > 0


def test_hidden_mechanisms_are_measured_without_changing_public_input() -> None:
    challenge = build_challenge()
    evaluation = evaluate_submission(challenge, example_submission(challenge, "backtest_winner"))
    hidden = evaluation["hidden_metrics"]
    assert hidden["liquidity_shock_sensitivity"] > 0
    assert hidden["one_day_delay_sensitivity"] < 0
    assert hidden["feature_collapse_sensitivity"] < 0
    assert hidden["regime_by_regime"]["structural_break"]["return"] < 0
    assert evaluation["public_metrics"]["estimated_transaction_costs"] > 0


def test_feedback_is_deterministic_and_discloses_limits() -> None:
    challenge = build_challenge()
    evaluation = evaluate_submission(challenge, example_submission(challenge, "backtest_winner"))
    first = deterministic_feedback(evaluation)
    second = deterministic_feedback(evaluation)
    assert first == second
    assert any(
        "deterministic" in item.lower() or "fictional" in item.lower()
        for item in first["feedback"]["limitations"]
    )


def test_gpt_challenge_path_requires_strict_structured_output() -> None:
    generation = ChallengeGeneration.model_validate(deterministic_generation())

    class FakeResponses:
        def parse(self, **kwargs):
            assert kwargs["text_format"] is ChallengeGeneration
            return type("Response", (), {"output_parsed": generation})()

    class FakeClient:
        responses = FakeResponses()

    result = generate_challenge_content("make a challenge", client=FakeClient(), model="gpt-test")
    assert result["status"] == "complete"
    assert result["mode"] == "gpt-5.6"
    assert result["content"]["hidden_scenario_specs"]
    with pytest.raises(ValueError):
        HiddenScenarioSpec(
            regime_type="unbounded_future_oracle",
            educational_purpose="bad",
            severity=0.5,
            rationale="bad",
        )


def test_gpt_feedback_path_is_structured_and_grounded() -> None:
    challenge = build_challenge()
    evaluation = evaluate_submission(challenge, example_submission(challenge, "backtest_winner"))
    fallback = deterministic_feedback(evaluation)
    parsed_feedback = StrategyFeedback.model_validate(fallback["feedback"])

    class FakeResponses:
        def parse(self, **kwargs):
            assert kwargs["text_format"] is StrategyFeedback
            return type("Response", (), {"output_parsed": parsed_feedback})()

    class FakeClient:
        responses = FakeResponses()

    result = generate_feedback(evaluation, client=FakeClient(), model="gpt-test")
    assert result["status"] == "complete"
    assert result["mode"] == "gpt-5.6"
    assert result["feedback"]["limitations"]


def test_arena_api_keeps_hidden_data_and_rankings_role_scoped(monkeypatch) -> None:
    monkeypatch.setenv("ARENA_TEST_AUTH", "1")
    client = TestClient(app)
    challenge_id = "api-arena-test"
    created = client.post(
        "/api/arena/challenges",
        headers={"X-Test-Role": "instructor"},
        json={"challenge_id": challenge_id, "mode": "offline", "prompt": "Create a quant challenge."},
    )
    assert created.status_code == 200
    assert client.get(f"/api/arena/challenges/{challenge_id}").status_code == 200
    assert (
        client.post(
            f"/api/arena/challenges/{challenge_id}/approve", headers={"X-Test-Role": "instructor"}
        ).status_code
        == 200
    )
    public = client.get(f"/api/arena/challenges/{challenge_id}/dataset").json()
    assert public["hidden_dates_included"] is False
    assert all("regime" not in row for row in public["rows"])
    assert client.get(f"/api/arena/challenges/{challenge_id}/instructor-report").status_code == 403
    report = client.get(
        f"/api/arena/challenges/{challenge_id}/instructor-report", headers={"X-Test-Role": "instructor"}
    )
    assert report.status_code == 200
    assert report.json()["hidden_regime_manifest"]
    a = example_submission(build_challenge(), "backtest_winner")
    b = example_submission(build_challenge(), "robust_generalizer")
    for name, csv_text in (("Example A", a), ("Example B", b)):
        response = client.post(
            f"/api/arena/challenges/{challenge_id}/submissions",
            json={
                "student_name": name,
                "csv_text": csv_text,
                "explanation": "A bounded classroom hypothesis.",
            },
        )
        assert response.status_code == 200
        assert "hidden_metrics" not in response.json()
    student_board = client.get(f"/api/arena/challenges/{challenge_id}/leaderboard").json()
    assert student_board["rows"][0]["public_score"] >= student_board["rows"][1]["public_score"]
    assert "robustness_score" not in student_board["rows"][0]
    instructor_board = client.get(
        f"/api/arena/challenges/{challenge_id}/leaderboard", headers={"X-Test-Role": "instructor"}
    ).json()
    assert instructor_board["rows"][0]["robustness_score"] >= instructor_board["rows"][1]["robustness_score"]
