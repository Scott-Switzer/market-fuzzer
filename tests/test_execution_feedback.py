from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.execution_feedback import (
    DEFAULT_MODEL,
    ExecutionEvidencePackage,
    ExecutionFeedback,
    FeedbackGroundingError,
    build_execution_evidence,
    generate_execution_feedback,
    validate_execution_feedback,
)

CORPUS_PATH = Path(__file__).parent / "fixtures" / "execution_feedback_eval_cases.json"


def _corpus() -> dict[str, Any]:
    return json.loads(CORPUS_PATH.read_text())


def _feedback_for_case(corpus: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    feedback = deepcopy(corpus["base_feedback"])
    feedback.update(deepcopy(case.get("feedback_patch", {})))
    return feedback


def _evidence_for_case(corpus: dict[str, Any], case: dict[str, Any]) -> ExecutionEvidencePackage:
    evidence = deepcopy(corpus["evidence"])
    if case.get("released", True) is False:
        evidence["released"] = False
        evidence["evidence_items"] = [
            item for item in evidence["evidence_items"] if item["phase"] in {"policy", "public"}
        ]
        evidence["event_ids"] = []
        evidence["trade_ids"] = []
        evidence["fill_ids"] = []
        evidence["replay_step_ids"] = []
        evidence["hidden_world_labels"] = []
        evidence["deterministic_outcome"].update(
            {"robustness_score": None, "robustness_rank": None, "rank_movement": None}
        )
    return ExecutionEvidencePackage.model_validate(evidence)


class FakeResponses:
    def __init__(self, mode: str, parsed: dict[str, Any]) -> None:
        self.mode = mode
        self.parsed = parsed
        self.calls = 0

    def parse(self, **kwargs: Any) -> Any:
        self.calls += 1
        assert kwargs["model"] == DEFAULT_MODEL
        assert kwargs["text_format"] is ExecutionFeedback
        assert kwargs["max_output_tokens"] == 4_000
        assert kwargs["input"][0]["role"] == "system"
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
    def __init__(self, mode: str, parsed: dict[str, Any]) -> None:
        self.responses = FakeResponses(mode, parsed)


def _case_ids(corpus: dict[str, Any]) -> list[str]:
    return [case["id"] for case in corpus["cases"]]


CORPUS = _corpus()


@pytest.mark.parametrize("case", CORPUS["cases"], ids=_case_ids(CORPUS))
def test_frozen_execution_feedback_eval_corpus(case: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    evidence = _evidence_for_case(CORPUS, case)
    feedback = _feedback_for_case(CORPUS, case)
    mode = case["response_mode"]
    client = None if mode == "no_key" else FakeClient(mode, feedback)

    result = generate_execution_feedback(evidence, client=client)

    assert result["status"] == case["expected_status"]
    assert result["scoring_authority"] == "deterministic_application_code"
    assert result["deterministic_outcome"] == evidence.deterministic_outcome.model_dump(mode="json")
    assert result["deterministic_outcome_hash"] == evidence.deterministic_outcome_hash
    if "expected_reason" in case:
        assert result["reason"] == case["expected_reason"]
    if result["status"] == "complete":
        assert result["generated_by"] == "openai_responses_api_structured_output"
        assert result["model"] == DEFAULT_MODEL
        assert validate_execution_feedback(result["feedback"], evidence)
    elif result["status"] == "withheld":
        assert client is not None
        assert client.responses.calls == 0
        serialized = json.dumps(result, sort_keys=True)
        for label in CORPUS["evidence"]["hidden_world_labels"]:
            assert label not in serialized
        assert all(item["phase"] in {"policy", "public"} for item in result["evidence"]["evidence_items"])
    else:
        assert result["generated_by"] == "deterministic_template"
        assert result["gpt_analysis_available"] is False
        assert "model_authored_robustness_score" not in result
        assert validate_execution_feedback(result["feedback"], evidence)


def test_frozen_corpus_covers_every_required_behavior() -> None:
    requirements = {case["requirement"] for case in CORPUS["cases"]}
    required = {
        "Correct latency-shock explanation",
        "Correct liquidity-withdrawal explanation",
        "Correct crowding explanation",
        "Correct ranking-reversal explanation",
        "Reject invented metrics",
        "Reject unknown evidence IDs",
        "Do not expose hidden results before release",
        "Do not change deterministic rank",
        "Do not make investment recommendations",
        "State limitations clearly",
        "Handle model refusal",
        "Handle incomplete structured response",
    }
    assert len(CORPUS["cases"]) >= 12
    assert required <= requirements


def test_strict_validator_rejects_direct_pre_release_use() -> None:
    case = next(case for case in CORPUS["cases"] if case["id"] == "withhold_before_release")
    evidence = _evidence_for_case(CORPUS, case)
    feedback = ExecutionFeedback.model_validate(CORPUS["base_feedback"])
    with pytest.raises(FeedbackGroundingError, match="withheld") as exc_info:
        validate_execution_feedback(feedback, evidence)
    assert exc_info.value.code == "hidden_not_released"


def test_matrix_projection_strips_protected_evidence_until_release() -> None:
    matrix = {
        "challenge": {
            "challenge_id": "trade-the-shock",
            "objective": "A bounded execution assessment.",
            "policies": [{"policy_id": "aggressive_pov", "participation_rate": 0.12, "latency_ms": 4}],
        },
        "rows": [
            {
                "policy_id": "aggressive_pov",
                "public_score": 998.5,
                "public_rank": 1,
                "public_shortfall_bps": 1.5,
                "public_completion_pct": 100.0,
                "robustness_score": 62.4,
                "robustness_rank": 2,
                "rank_movement": -1,
                "hidden_mean_shortfall_bps": 81.2,
                "hidden_worst_shortfall_bps": 140.7,
                "hidden_completion_pct": 96.0,
                "released_intent_aggregates": {
                    "message_latency": {
                        "implementation_shortfall_bps": 120.4,
                        "order_entry_latency_ms": 40.0,
                    }
                },
                "world_results": [
                    {
                        "variant": "latency_shock",
                        "seed": 42,
                        "metrics": {
                            "implementation_shortfall_bps": 120.4,
                            "completion_pct": 92.0,
                        },
                    }
                ],
            },
            {
                "policy_id": "guarded_pov",
                "public_score": 990.0,
                "public_rank": 2,
                "robustness_score": 78.9,
                "robustness_rank": 1,
                "rank_movement": 1,
                "hidden_mean_shortfall_bps": 42.1,
                "hidden_worst_shortfall_bps": 70.0,
                "hidden_completion_pct": 98.0,
                "world_results": [],
            },
        ],
    }

    public = build_execution_evidence(matrix, "aggressive_pov", released=False)
    assert public.hidden_world_labels == []
    assert public.deterministic_outcome.robustness_rank is None
    assert all(item.phase in {"policy", "public"} for item in public.evidence_items)
    serialized = json.dumps(public.model_payload(), sort_keys=True)
    assert "latency_shock" not in serialized
    assert "hidden_mean_shortfall_bps" not in serialized

    released_matrix = deepcopy(matrix)
    for row in released_matrix["rows"]:
        row.pop("world_results", None)
    released = build_execution_evidence(released_matrix, "aggressive_pov", released=True)
    assert released.hidden_world_labels == ["message_latency"]
    assert released.deterministic_outcome.robustness_rank == 2
    assert any(item.phase == "hidden" for item in released.evidence_items)
    assert any(item.phase == "comparison" for item in released.evidence_items)
    assert any(item.metric_name == "order_entry_latency_ms" for item in released.evidence_items)
    released_serialized = json.dumps(released.model_payload(), sort_keys=True)
    assert "latency_shock" not in released_serialized
    assert "message_latency" in released_serialized
