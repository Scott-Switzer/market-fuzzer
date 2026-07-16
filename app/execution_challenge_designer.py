"""Structured GPT-5.6 educational design for execution challenges.

The model may propose framing and approved hidden-test intents.  It never
constructs numeric worlds or determines prices, orders, fills, metrics, scores,
ranks, or release state; those remain deterministic application concerns.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

DEFAULT_MODEL = "gpt-5.6"
InterventionId = Literal[
    "liquidity_withdrawal",
    "crowded_unwind",
    "earnings_shock",
    "latency_shock",
]
SeverityBand = Literal["low", "moderate", "high"]
Difficulty = Literal["introductory", "intermediate", "advanced"]

ALLOWED_INTERVENTION_IDS = frozenset(
    {"liquidity_withdrawal", "crowded_unwind", "earnings_shock", "latency_shock"}
)
ALLOWED_POLICY_PARAMETER_IDS = frozenset(
    {
        "strategy_type",
        "target_participation",
        "max_participation",
        "max_spread_bps",
        "urgency_curve",
        "feed_latency_tolerance_ms",
        "cancel_after_ms",
        "completion_buffer_steps",
        "pause_during_halt",
        "pause_above_spread_limit",
        "include_pending_in_budget",
    }
)
_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?(?![A-Za-z0-9_])")
_OUTCOME_CLAIM_PATTERN = re.compile(
    r"\b(?:rank(?:ed)?\s+(?:first|second|third|highest|lowest)|"
    r"(?:wins?|loses?|beats?)\s+(?:the\s+)?(?:public|hidden|protected)?\s*(?:rank|leaderboard)|"
    r"guaranteed\s+(?:profit|return|performance))\b",
    re.IGNORECASE,
)


class ExecutionChallengeDesignInput(BaseModel):
    """Instructor-owned constraints supplied to the educational designer."""

    model_config = ConfigDict(extra="forbid")

    course_level: str = Field(min_length=2, max_length=120)
    learning_objective: str = Field(min_length=10, max_length=1_000)
    exchange_capabilities: list[str] = Field(min_length=1, max_length=20)
    allowed_world_interventions: list[InterventionId] = Field(min_length=1, max_length=4)
    allowed_policy_parameters: list[str] = Field(min_length=1, max_length=20)
    difficulty: Difficulty

    @field_validator("exchange_capabilities")
    @classmethod
    def unique_capabilities(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 240 for value in cleaned):
            raise ValueError("exchange capabilities must be non-empty and at most 240 characters")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("exchange capabilities must be unique")
        return cleaned

    @field_validator("allowed_world_interventions", "allowed_policy_parameters")
    @classmethod
    def unique_identifiers(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("allowed identifiers must be unique")
        return values

    @field_validator("allowed_policy_parameters")
    @classmethod
    def valid_policy_parameters(cls, values: list[str]) -> list[str]:
        unknown = sorted(set(values) - ALLOWED_POLICY_PARAMETER_IDS)
        if unknown:
            raise ValueError("unsupported policy parameter IDs: " + ", ".join(unknown))
        return values


class HiddenTestIntent(BaseModel):
    """An approved educational intent that deterministic code maps to a world."""

    model_config = ConfigDict(extra="forbid")

    intervention_id: InterventionId
    educational_purpose: str = Field(min_length=10, max_length=700)
    severity_band: SeverityBand
    rationale: str = Field(min_length=10, max_length=700)


class ExecutionChallengeDesign(BaseModel):
    """Qualitative challenge content with no numeric market outcomes."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=160)
    student_brief: str = Field(min_length=30, max_length=3_000)
    learning_objectives: list[str] = Field(min_length=1, max_length=8)
    public_world_narrative: str = Field(min_length=20, max_length=1_500)
    hidden_test_intents: list[HiddenTestIntent] = Field(min_length=1, max_length=4)
    expected_misconceptions: list[str] = Field(min_length=1, max_length=8)
    instructor_rubric: list[str] = Field(min_length=1, max_length=8)
    limitations: list[str] = Field(min_length=2, max_length=8)

    @field_validator("learning_objectives", "expected_misconceptions", "instructor_rubric", "limitations")
    @classmethod
    def clean_text_lists(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(len(value) < 5 or len(value) > 700 for value in cleaned):
            raise ValueError("design list items must contain 5 to 700 characters")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("design list items must be unique")
        return cleaned

    @model_validator(mode="after")
    def unique_hidden_intents(self) -> ExecutionChallengeDesign:
        identifiers = [intent.intervention_id for intent in self.hidden_test_intents]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("hidden test intervention IDs must be unique")
        return self


class ResponsesClient(Protocol):
    responses: Any


class ChallengeDesignValidationError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


CHALLENGE_DESIGN_PROMPT = """You are the Quant Challenge Arena execution-challenge designer.
Return only the requested structured educational design. Use intervention_id values only from the
request's allowed_world_interventions and policy parameter names only from
allowed_policy_parameters. Hidden intents describe educational purpose, severity band, and
qualitative rationale; deterministic application code maps them to numeric worlds.

Do not produce any number in the design. Do not produce prices, quantities, order values, fill
values, latencies, participation targets, seeds, metrics, scores, ranks, ranking outcomes, hidden
result values, or release decisions. Do not claim profitability, investment suitability,
production safety, or live-trading validity. State that the exchange is deterministic and
synthetic, and that deterministic code owns outcomes."""


_INTENT_TEXT = {
    "liquidity_withdrawal": (
        "Test whether the policy responds appropriately when displayed liquidity contracts.",
        "Visible liquidity can encourage overconfident participation unless the policy respects depth and spread controls.",
    ),
    "crowded_unwind": (
        "Test whether the policy remains controlled when directional crowd pressure changes executable flow.",
        "A policy tuned to cooperative background flow may become fragile when many participants seek the same side.",
    ),
    "earnings_shock": (
        "Test whether the policy handles a scheduled synthetic event without treating the public path as permanent.",
        "A discrete event separates event awareness from simple continuation of visible practice behavior.",
    ),
    "latency_shock": (
        "Test whether feed, decision, order-entry, and cancel timing change the policy's execution behavior.",
        "A policy should reason about the message lifecycle rather than assume observations and actions are simultaneous.",
    ),
}


def _all_design_text(design: ExecutionChallengeDesign) -> list[str]:
    return [
        design.title,
        design.student_brief,
        *design.learning_objectives,
        design.public_world_narrative,
        *(
            value
            for intent in design.hidden_test_intents
            for value in (intent.educational_purpose, intent.rationale)
        ),
        *design.expected_misconceptions,
        *design.instructor_rubric,
        *design.limitations,
    ]


def validate_execution_challenge_design(
    design: ExecutionChallengeDesign | Mapping[str, Any],
    constraints: ExecutionChallengeDesignInput | Mapping[str, Any],
) -> ExecutionChallengeDesign:
    parsed = (
        design
        if isinstance(design, ExecutionChallengeDesign)
        else ExecutionChallengeDesign.model_validate(design)
    )
    request = (
        constraints
        if isinstance(constraints, ExecutionChallengeDesignInput)
        else ExecutionChallengeDesignInput.model_validate(constraints)
    )
    used = {intent.intervention_id for intent in parsed.hidden_test_intents}
    unknown = sorted(used - set(request.allowed_world_interventions))
    if unknown:
        raise ChallengeDesignValidationError(
            "intervention_not_allowed",
            "challenge design used an intervention outside the instructor allow-list",
        )

    texts = _all_design_text(parsed)
    combined_text = " ".join(texts)
    disallowed_parameters = sorted(
        parameter
        for parameter in ALLOWED_POLICY_PARAMETER_IDS - set(request.allowed_policy_parameters)
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(parameter)}(?![A-Za-z0-9_])", combined_text)
    )
    if disallowed_parameters:
        raise ChallengeDesignValidationError(
            "policy_parameter_not_allowed",
            "challenge design used a policy parameter outside the instructor allow-list",
        )
    if any(_NUMBER_PATTERN.search(text) for text in texts):
        raise ChallengeDesignValidationError(
            "numeric_design_value",
            "challenge design contained a number reserved for deterministic world construction",
        )
    if any(_OUTCOME_CLAIM_PATTERN.search(text) for text in texts):
        raise ChallengeDesignValidationError(
            "model_authored_outcome",
            "challenge design asserted a score, rank, leaderboard, or profitability outcome",
        )
    limitations = " ".join(parsed.limitations).lower()
    if not ("deterministic" in limitations and ("synthetic" in limitations or "fictional" in limitations)):
        raise ChallengeDesignValidationError(
            "missing_deterministic_limitation",
            "challenge design must disclose its deterministic synthetic boundary",
        )
    if not (
        any(term in limitations for term in ("does not", "cannot", "not ", "never"))
        and any(term in limitations for term in ("profit", "investment", "production", "live-trading"))
    ):
        raise ChallengeDesignValidationError(
            "missing_use_limitation",
            "challenge design must reject profitability, investment, production, or live-trading conclusions",
        )
    return parsed


def deterministic_execution_challenge_design(
    constraints: ExecutionChallengeDesignInput,
) -> ExecutionChallengeDesign:
    severity_by_difficulty: dict[Difficulty, SeverityBand] = {
        "introductory": "low",
        "intermediate": "moderate",
        "advanced": "high",
    }
    severity = severity_by_difficulty[constraints.difficulty]
    intents = [
        HiddenTestIntent(
            intervention_id=intervention_id,
            educational_purpose=_INTENT_TEXT[intervention_id][0],
            severity_band=severity,
            rationale=_INTENT_TEXT[intervention_id][1],
        )
        for intervention_id in constraints.allowed_world_interventions
    ]
    learning_objective = _NUMBER_PATTERN.sub("bounded", constraints.learning_objective)
    design = ExecutionChallengeDesign(
        title="Execution Robustness Under Protected Conditions",
        student_brief=(
            "Configure a declarative execution policy in the visible synthetic exchange, explain its market-behavior hypothesis, and submit a final immutable policy for protected robustness assessment."
        ),
        learning_objectives=[
            learning_objective,
            "Distinguish visible practice performance from robustness across controlled market conditions.",
            "Use replay evidence to connect policy controls with message timing, liquidity, and completion behavior.",
        ],
        public_world_narrative=(
            "The visible world provides stable background flow and a readable order book so participants can inspect how their declarative policy behaves before final submission."
        ),
        hidden_test_intents=intents,
        expected_misconceptions=[
            "A policy that looks strong in the visible world must also be robust elsewhere.",
            "Observations, decisions, order entry, cancellation, and fills occur simultaneously.",
            "Completion alone is sufficient evidence of good execution behavior.",
        ],
        instructor_rubric=[
            "Connects policy controls to measured execution evidence.",
            "Distinguishes public practice from protected robustness assessment.",
            "States limitations and proposes a bounded reproducible next experiment.",
        ],
        limitations=[
            "The exchange is deterministic and synthetic; it is an educational assessment environment.",
            "The design does not establish profitability, investment suitability, production safety, or live-trading validity.",
            "Deterministic application code owns worlds, orders, fills, metrics, scores, ranks, and release state.",
        ],
    )
    return validate_execution_challenge_design(design, constraints)


def _item_value(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, Mapping) else getattr(item, key, None)


def _response_refusal(response: Any) -> bool:
    output = getattr(response, "output", None)
    if not isinstance(output, Sequence):
        return False
    for message in output:
        content = _item_value(message, "content")
        if not isinstance(content, Sequence):
            continue
        if any(_item_value(item, "type") == "refusal" for item in content):
            return True
    return False


def _fallback_result(
    constraints: ExecutionChallengeDesignInput,
    *,
    status: Literal["unavailable", "refused", "incomplete", "invalid"],
    message: str,
    reason: str,
    model: str | None,
) -> dict[str, Any]:
    design = deterministic_execution_challenge_design(constraints)
    return {
        "status": status,
        "mode": "deterministic_fallback",
        "generated_by": "deterministic_template",
        "gpt_design_available": False,
        "model": model,
        "message": message,
        "reason": reason,
        "design": design.model_dump(mode="json"),
        "allowed_intervention_ids": list(constraints.allowed_world_interventions),
        "world_construction_authority": "deterministic_application_code",
    }


def generate_execution_challenge_design(
    constraints: ExecutionChallengeDesignInput | Mapping[str, Any],
    *,
    client: ResponsesClient | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Generate a validated design or an explicitly labeled deterministic fallback."""
    request = (
        constraints
        if isinstance(constraints, ExecutionChallengeDesignInput)
        else ExecutionChallengeDesignInput.model_validate(constraints)
    )
    selected_model = model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
    if client is None and not (api_key or os.getenv("OPENAI_API_KEY")):
        return _fallback_result(
            request,
            status="unavailable",
            message="GPT-5.6 challenge design is unavailable because no OpenAI API key is configured.",
            reason="missing_api_key",
            model=None,
        )

    active_client: Any = client
    if active_client is None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("OpenAI SDK is not installed; use the no-key fallback") from exc
        active_client = OpenAI(api_key=api_key, timeout=30.0, max_retries=2)

    response = active_client.responses.parse(
        model=selected_model,
        input=[
            {"role": "system", "content": CHALLENGE_DESIGN_PROMPT},
            {
                "role": "user",
                "content": "Design within these instructor-owned constraints:\n"
                + json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
            },
        ],
        text_format=ExecutionChallengeDesign,
        max_output_tokens=4_000,
    )
    if _response_refusal(response):
        return _fallback_result(
            request,
            status="refused",
            message="GPT-5.6 refused the design request; deterministic challenge content is shown instead.",
            reason="model_refusal",
            model=selected_model,
        )
    if (
        getattr(response, "status", None) == "incomplete"
        or getattr(response, "incomplete_details", None) is not None
    ):
        return _fallback_result(
            request,
            status="incomplete",
            message="GPT-5.6 returned an incomplete response; deterministic challenge content is shown instead.",
            reason="incomplete_response",
            model=selected_model,
        )

    output_parsed = getattr(response, "output_parsed", None)
    if output_parsed is None:
        return _fallback_result(
            request,
            status="incomplete",
            message="GPT-5.6 returned no complete structured design; deterministic challenge content is shown instead.",
            reason="missing_structured_output",
            model=selected_model,
        )
    try:
        parsed = (
            output_parsed
            if isinstance(output_parsed, ExecutionChallengeDesign)
            else ExecutionChallengeDesign.model_validate(output_parsed)
        )
    except ValidationError:
        return _fallback_result(
            request,
            status="incomplete",
            message="GPT-5.6 returned an incomplete structured design; deterministic challenge content is shown instead.",
            reason="invalid_structured_output",
            model=selected_model,
        )
    try:
        validated = validate_execution_challenge_design(parsed, request)
    except ChallengeDesignValidationError as exc:
        return _fallback_result(
            request,
            status="invalid",
            message="GPT-5.6 design failed deterministic validation; fallback content is shown instead.",
            reason=exc.code,
            model=selected_model,
        )
    return {
        "status": "complete",
        "mode": "gpt-5.6",
        "generated_by": "openai_responses_api_structured_output",
        "gpt_design_available": True,
        "model": selected_model,
        "design": validated.model_dump(mode="json"),
        "allowed_intervention_ids": list(request.allowed_world_interventions),
        "world_construction_authority": "deterministic_application_code",
    }
