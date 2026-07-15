from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from app.execution_challenge_designer import (
    DEFAULT_MODEL,
    ExecutionChallengeDesign,
    ExecutionChallengeDesignInput,
    generate_execution_challenge_design,
    validate_execution_challenge_design,
)


def _constraints(**updates: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "course_level": "graduate",
        "learning_objective": "Compare execution-policy robustness across controlled market conditions.",
        "exchange_capabilities": ["limit orders", "cancellation messages", "deterministic replay"],
        "allowed_world_interventions": ["latency_shock", "liquidity_withdrawal"],
        "allowed_policy_parameters": [
            "target_participation",
            "max_spread_bps",
            "feed_latency_tolerance_ms",
            "cancel_after_ms",
        ],
        "difficulty": "advanced",
    }
    values.update(updates)
    return values


def _valid_design(**updates: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "title": "Execution Policy Robustness",
        "student_brief": (
            "Configure a bounded execution policy, explain the behavioral hypothesis, and use replay "
            "evidence to assess its robustness."
        ),
        "learning_objectives": [
            "Connect policy controls to observed message-lifecycle evidence.",
            "Distinguish visible practice from protected robustness assessment.",
        ],
        "public_world_narrative": (
            "A stable synthetic order book provides a visible practice setting for policy inspection."
        ),
        "hidden_test_intents": [
            {
                "intervention_id": "latency_shock",
                "educational_purpose": "Test reasoning about observation and action timing.",
                "severity_band": "high",
                "rationale": "Delayed messages expose policies that assume simultaneous exchange activity.",
            }
        ],
        "expected_misconceptions": [
            "Visible practice leadership guarantees robustness elsewhere.",
            "Exchange observations and actions occur simultaneously.",
        ],
        "instructor_rubric": [
            "Links approved policy controls to replay evidence.",
            "Separates deterministic outcomes from qualitative interpretation.",
        ],
        "limitations": [
            "The exchange is deterministic and synthetic; its evidence is educational.",
            "The design does not establish profitability, investment suitability, production safety, "
            "or live-trading validity.",
            "Deterministic application code owns worlds and all outcomes.",
        ],
    }
    values.update(updates)
    return values


class FakeResponses:
    def __init__(self, mode: str, parsed: dict[str, Any] | None) -> None:
        self.mode = mode
        self.parsed = parsed
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.mode == "refusal":
            return type(
                "Response",
                (),
                {
                    "status": "completed",
                    "incomplete_details": None,
                    "output_parsed": None,
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "refusal", "refusal": "I cannot assist."}],
                        }
                    ],
                },
            )()
        if self.mode == "incomplete":
            return type(
                "Response",
                (),
                {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "output_parsed": None,
                    "output": [],
                },
            )()
        if self.mode == "missing_parsed":
            return type(
                "Response",
                (),
                {
                    "status": "completed",
                    "incomplete_details": None,
                    "output_parsed": None,
                    "output": [],
                },
            )()
        return type(
            "Response",
            (),
            {
                "status": "completed",
                "incomplete_details": None,
                "output_parsed": self.parsed,
                "output": [],
            },
        )()


class FakeClient:
    def __init__(self, mode: str = "complete", parsed: dict[str, Any] | None = None) -> None:
        self.responses = FakeResponses(mode, parsed)


def test_no_key_path_is_explicit_deterministic_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    constraints = _constraints(
        learning_objective="Compare execution behavior across 3 controlled interventions."
    )

    result = generate_execution_challenge_design(constraints)

    assert result["status"] == "unavailable"
    assert result["mode"] == "deterministic_fallback"
    assert result["generated_by"] == "deterministic_template"
    assert result["gpt_design_available"] is False
    assert result["reason"] == "missing_api_key"
    assert result["world_construction_authority"] == "deterministic_application_code"
    assert {intent["intervention_id"] for intent in result["design"]["hidden_test_intents"]} == {
        "latency_shock",
        "liquidity_withdrawal",
    }
    assert "3" not in str(result["design"])


def test_valid_structured_output_uses_responses_parse() -> None:
    client = FakeClient(parsed=_valid_design())

    result = generate_execution_challenge_design(_constraints(), client=client)

    assert result["status"] == "complete"
    assert result["mode"] == "gpt-5.6"
    assert result["generated_by"] == "openai_responses_api_structured_output"
    assert result["model"] == DEFAULT_MODEL
    assert result["world_construction_authority"] == "deterministic_application_code"
    assert validate_execution_challenge_design(result["design"], _constraints())
    call = client.responses.calls[0]
    assert call["model"] == DEFAULT_MODEL
    assert call["text_format"] is ExecutionChallengeDesign
    assert call["max_output_tokens"] == 4_000
    assert call["input"][0]["role"] == "system"
    assert call["input"][1]["role"] == "user"


@pytest.mark.parametrize(
    ("mode", "expected_status", "expected_reason"),
    [
        ("refusal", "refused", "model_refusal"),
        ("incomplete", "incomplete", "incomplete_response"),
        ("missing_parsed", "incomplete", "missing_structured_output"),
    ],
)
def test_refusal_and_incomplete_outputs_use_labeled_fallback(
    mode: str,
    expected_status: str,
    expected_reason: str,
) -> None:
    client = FakeClient(mode=mode)

    result = generate_execution_challenge_design(_constraints(), client=client)

    assert result["status"] == expected_status
    assert result["reason"] == expected_reason
    assert result["mode"] == "deterministic_fallback"
    assert result["generated_by"] == "deterministic_template"
    assert result["gpt_design_available"] is False


@pytest.mark.parametrize(
    ("mutator", "expected_status", "expected_reason"),
    [
        (
            lambda design: design["hidden_test_intents"][0].update({"intervention_id": "crowded_unwind"}),
            "invalid",
            "intervention_not_allowed",
        ),
        (
            lambda design: design.update(
                {"student_brief": "Configure 42 orders and inspect their policy behavior carefully."}
            ),
            "invalid",
            "numeric_design_value",
        ),
        (
            lambda design: design["instructor_rubric"].append(
                "Tune include_pending_in_budget after reviewing the replay."
            ),
            "invalid",
            "policy_parameter_not_allowed",
        ),
    ],
)
def test_deterministic_validator_enforces_design_authority(
    mutator: Any,
    expected_status: str,
    expected_reason: str,
) -> None:
    design = deepcopy(_valid_design())
    mutator(design)
    client = FakeClient(parsed=design)

    result = generate_execution_challenge_design(_constraints(), client=client)

    assert result["status"] == expected_status
    assert result["reason"] == expected_reason
    assert result["generated_by"] == "deterministic_template"


def test_rank_or_profit_outcome_claim_is_rejected() -> None:
    design = _valid_design(
        student_brief=(
            "Configure a bounded execution policy and prove that it ranked first on the protected leaderboard."
        )
    )

    result = generate_execution_challenge_design(_constraints(), client=FakeClient(parsed=design))

    assert result["status"] == "invalid"
    assert result["reason"] == "model_authored_outcome"


def test_missing_required_limitation_is_rejected() -> None:
    design = _valid_design(
        limitations=[
            "This classroom artifact provides qualitative educational guidance only.",
            "It does not establish profitability or live-trading validity.",
        ]
    )

    result = generate_execution_challenge_design(_constraints(), client=FakeClient(parsed=design))

    assert result["status"] == "invalid"
    assert result["reason"] == "missing_deterministic_limitation"


def test_extra_model_field_is_invalid_structured_output() -> None:
    design = _valid_design(model_authored_score=99)

    result = generate_execution_challenge_design(_constraints(), client=FakeClient(parsed=design))

    assert result["status"] == "incomplete"
    assert result["reason"] == "invalid_structured_output"


@pytest.mark.parametrize(
    "updates",
    [
        {"allowed_policy_parameters": ["unknown_policy_control"]},
        {"allowed_world_interventions": ["unknown_world"]},
        {"allowed_world_interventions": ["latency_shock", "latency_shock"]},
        {"allowed_policy_parameters": ["max_spread_bps", "max_spread_bps"]},
    ],
)
def test_instructor_constraints_reject_unknown_or_duplicate_ids(updates: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ExecutionChallengeDesignInput.model_validate(_constraints(**updates))
