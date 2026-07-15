"""Grounded GPT-5.6 feedback for deterministic execution-challenge results.

The exchange, evaluation matrix, scores, ranks, and release state are owned by
deterministic application code.  This module gives a model a deliberately small
interpretation role after release and rejects output that is not traceable to
the supplied evidence package.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from hashlib import sha256
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

DEFAULT_MODEL = "gpt-5.6"
DEFAULT_LIMITATIONS = (
    "This is a deterministic synthetic exchange built for education and reproducible assessment.",
    "The evidence does not establish profitability, investment suitability, production safety, or live-trading performance.",
)
_ID_PATTERN = r"^[a-z0-9][a-z0-9._:-]{2,180}$"
_METRIC_PATTERN = r"^[a-z][a-z0-9_]{1,100}$"
_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?(?![A-Za-z0-9_])")
_METRIC_TOKEN_PATTERN = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_WORLD_TOKEN_PATTERN = re.compile(r"\b[a-z][a-z0-9_]*(?:shock|withdrawal|unwind|crash|regime|world)\b")
_NEGATION_PATTERN = re.compile(r"\b(?:not|never|cannot|can't|does not|doesn't|isn't|is not)\b")
_RANK_ASSERTION_PATTERN = re.compile(
    r"\b(?:rank(?:ed)?\s+(?:first|second|third|fourth|fifth)|"
    r"(?:first|second|third|fourth|fifth)(?:-place)?\s+(?:public|robustness|hidden)?\s*rank)\b"
)


Scalar = str | int | float | bool


class EvidenceItem(BaseModel):
    """One verified scalar with a stable identifier and canonical metric name."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(pattern=_ID_PATTERN)
    metric_name: str = Field(pattern=_METRIC_PATTERN)
    value: int | float
    phase: Literal["policy", "public", "hidden", "comparison"]
    context: str = Field(min_length=1, max_length=240)

    @field_validator("value")
    @classmethod
    def finite_number(cls, value: int | float) -> int | float:
        if isinstance(value, bool) or not math.isfinite(float(value)):
            raise ValueError("evidence values must be finite numbers")
        return value


class DeterministicOutcome(BaseModel):
    """Scores and ranks that GPT is never allowed to replace or mutate."""

    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,100}$")
    public_score: float
    public_rank: int = Field(ge=1)
    robustness_score: float | None = None
    robustness_rank: int | None = Field(default=None, ge=1)
    rank_movement: int | None = None


class ExecutionEvidencePackage(BaseModel):
    """The complete allow-list for one model feedback request."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    challenge_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,100}$")
    challenge_objective: str = Field(min_length=1, max_length=1_500)
    policy_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,100}$")
    policy_parameters: dict[str, Scalar] = Field(default_factory=dict, max_length=40)
    released: bool
    evidence_items: list[EvidenceItem] = Field(min_length=1, max_length=300)
    event_ids: list[str] = Field(default_factory=list, max_length=200)
    trade_ids: list[str] = Field(default_factory=list, max_length=500)
    fill_ids: list[str] = Field(default_factory=list, max_length=500)
    replay_step_ids: list[str] = Field(default_factory=list, max_length=500)
    hidden_world_labels: list[str] = Field(default_factory=list, max_length=40)
    deterministic_outcome: DeterministicOutcome
    limitations: list[str] = Field(min_length=1, max_length=12)

    @field_validator("event_ids", "trade_ids", "fill_ids", "replay_step_ids")
    @classmethod
    def valid_trace_ids(cls, values: list[str]) -> list[str]:
        pattern = re.compile(_ID_PATTERN)
        if len(values) != len(set(values)):
            raise ValueError("trace evidence IDs must be unique within each category")
        if any(pattern.fullmatch(value) is None for value in values):
            raise ValueError("trace evidence IDs must use the stable evidence-ID format")
        return values

    @model_validator(mode="after")
    def release_boundary_and_identity(self) -> ExecutionEvidencePackage:
        item_ids = [item.evidence_id for item in self.evidence_items]
        all_ids = item_ids + self.event_ids + self.trade_ids + self.fill_ids + self.replay_step_ids
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("all evidence IDs in a package must be unique")
        if self.policy_id != self.deterministic_outcome.policy_id:
            raise ValueError("policy_id must match the deterministic outcome")
        protected_items = [item for item in self.evidence_items if item.phase in {"hidden", "comparison"}]
        hidden_outcome = (
            self.deterministic_outcome.robustness_score,
            self.deterministic_outcome.robustness_rank,
            self.deterministic_outcome.rank_movement,
        )
        if not self.released and (
            protected_items or self.hidden_world_labels or any(v is not None for v in hidden_outcome)
        ):
            raise ValueError("unreleased evidence packages cannot contain hidden evidence")
        return self

    @property
    def allowed_evidence_ids(self) -> set[str]:
        return {
            *(item.evidence_id for item in self.evidence_items),
            *self.event_ids,
            *self.trade_ids,
            *self.fill_ids,
            *self.replay_step_ids,
        }

    @property
    def allowed_metric_names(self) -> set[str]:
        return {item.metric_name for item in self.evidence_items}

    @property
    def deterministic_outcome_hash(self) -> str:
        payload = self.deterministic_outcome.model_dump(mode="json")
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def model_payload(self) -> dict[str, Any]:
        """Serialize the evidence plus explicit allow-lists for the model."""
        payload = self.model_dump(mode="json")
        payload["allowed_evidence_ids"] = sorted(self.allowed_evidence_ids)
        payload["allowed_metric_names"] = sorted(self.allowed_metric_names)
        payload["deterministic_outcome_hash"] = self.deterministic_outcome_hash
        return payload


class EvidenceStatement(BaseModel):
    """A claim whose IDs, metric names, and numbers can all be checked."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=1_000)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)
    metric_names: list[str] = Field(min_length=1, max_length=12)
    numeric_values: list[float] = Field(min_length=1, max_length=12)

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("evidence_ids must be unique")
        return values

    @field_validator("metric_names")
    @classmethod
    def unique_metric_names(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("metric_names must be unique")
        return values

    @field_validator("numeric_values")
    @classmethod
    def finite_numeric_values(cls, values: list[float]) -> list[float]:
        if any(not math.isfinite(value) for value in values):
            raise ValueError("numeric_values must be finite")
        return values


class ExecutionFeedback(BaseModel):
    """Structured educational analysis; intentionally contains no score fields."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=1_200)
    public_strengths: list[EvidenceStatement] = Field(default_factory=list, max_length=8)
    hidden_failures: list[EvidenceStatement] = Field(default_factory=list, max_length=10)
    why_public_rank_changed: str = Field(min_length=1, max_length=1_500)
    why_robust_policy_survived: str = Field(min_length=1, max_length=1_500)
    recommended_policy_changes: list[str] = Field(min_length=1, max_length=8)
    next_experiments: list[str] = Field(min_length=1, max_length=8)
    limitations: list[str] = Field(min_length=2, max_length=10)


class ResponsesClient(Protocol):
    responses: Any


class FeedbackGroundingError(ValueError):
    """A stable error code for model-output rejection without echoing its text."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


EXECUTION_FEEDBACK_PROMPT = """You are the Quant Challenge Arena evidence analyst.
Explain only the verified evidence package supplied by the application. Deterministic application
code is authoritative for all market events, orders, fills, metrics, scores, ranks, and release
state. You cannot change, recompute, or contradict those values.

For every quantitative EvidenceStatement:
- copy every evidence ID exactly from allowed_evidence_ids;
- use canonical metric names exactly from allowed_metric_names;
- copy numeric values exactly from the referenced evidence items;
- write each canonical metric name beside its numeric value in the statement.

Do not invent worlds, metrics, values, causal certainty, profitability, investment advice,
production safety, or live-trading claims. Recommend only bounded policy changes and reproducible
synthetic experiments. State both the deterministic synthetic limitation and the inability to
establish profitability, investment suitability, production safety, or live-trading performance.
Return only the requested structured output."""


def _number_equal(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=1e-9)


def _human_text(feedback: ExecutionFeedback) -> list[str]:
    return [
        feedback.summary,
        *(statement.statement for statement in feedback.public_strengths),
        *(statement.statement for statement in feedback.hidden_failures),
        feedback.why_public_rank_changed,
        feedback.why_robust_policy_survived,
        *feedback.recommended_policy_changes,
        *feedback.next_experiments,
        *feedback.limitations,
    ]


def _parsed_numbers(text: str) -> list[float]:
    return [float(match.group()) for match in _NUMBER_PATTERN.finditer(text)]


def _metric_value_pairs(text: str, metric_names: Iterable[str]) -> set[tuple[str, float]]:
    pairs: set[tuple[str, float]] = set()
    number = r"(-?\d+(?:\.\d+)?)"
    for metric_name in metric_names:
        escaped = re.escape(metric_name)
        forward = re.compile(rf"\b{escaped}\b[^.;\n]{{0,32}}?{number}")
        pairs.update((metric_name, float(match.group(1))) for match in forward.finditer(text))
    return pairs


def _claim_is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 60) : start]
    prefix = re.split(r"[.;\n]|\b(?:but|however|yet)\b", prefix)[-1]
    return _NEGATION_PATTERN.search(prefix) is not None


def _validate_safety_claims(texts: Iterable[str]) -> None:
    joined = "\n".join(texts).lower()
    positive_patterns = (
        re.compile(
            r"\b(?:is|are|was|will be|appears|proves?|guarantees?|ensures?)\b[^.\n]{0,45}\bprofitable\b"
        ),
        re.compile(r"\b(?:proves?|guarantees?|ensures?|establishes?)\b[^.\n]{0,35}\bprofitability\b"),
        re.compile(r"\bproduction[- ](?:ready|safe)\b"),
        re.compile(r"\bsafe for (?:production|live trading)\b"),
        re.compile(r"\b(?:investment advice|investment recommendation)\b"),
        re.compile(r"\b(?:you should|we recommend|i recommend)\s+(?:buy|sell|hold|invest)\b"),
        re.compile(r"\b(?:guaranteed returns?|will make money|will outperform)\b"),
    )
    for pattern in positive_patterns:
        for match in pattern.finditer(joined):
            if not _claim_is_negated(joined, match.start()):
                raise FeedbackGroundingError(
                    "unsafe_financial_or_production_claim",
                    "feedback made an investment, profitability, or production-safety claim",
                )


def _validate_statement(
    statement: EvidenceStatement,
    evidence: ExecutionEvidencePackage,
) -> None:
    unknown_ids = sorted(set(statement.evidence_ids) - evidence.allowed_evidence_ids)
    if unknown_ids:
        raise FeedbackGroundingError("unknown_evidence_id", "feedback referenced an unknown evidence ID")
    unknown_metrics = sorted(set(statement.metric_names) - evidence.allowed_metric_names)
    if unknown_metrics:
        raise FeedbackGroundingError("unknown_metric_name", "feedback referenced an unknown metric name")

    referenced = [item for item in evidence.evidence_items if item.evidence_id in set(statement.evidence_ids)]
    referenced_metrics = {item.metric_name for item in referenced}
    if not set(statement.metric_names).issubset(referenced_metrics):
        raise FeedbackGroundingError(
            "metric_not_bound_to_evidence",
            "a metric name was not supplied by the referenced evidence IDs",
        )
    referenced_values = [float(item.value) for item in referenced]
    for value in statement.numeric_values:
        if not any(_number_equal(value, candidate) for candidate in referenced_values):
            raise FeedbackGroundingError(
                "number_not_bound_to_evidence",
                "a numeric value was not supplied by the referenced evidence IDs",
            )

    text_numbers = _parsed_numbers(statement.statement)
    for value in text_numbers:
        if not any(_number_equal(value, candidate) for candidate in statement.numeric_values):
            raise FeedbackGroundingError(
                "undeclared_numeric_claim",
                "a statement number was not declared in numeric_values",
            )
    for value in statement.numeric_values:
        if not any(_number_equal(value, candidate) for candidate in text_numbers):
            raise FeedbackGroundingError(
                "unused_numeric_value",
                "numeric_values contained a number not present in the statement",
            )

    pairs = _metric_value_pairs(statement.statement, statement.metric_names)
    referenced_pairs = {(item.metric_name, float(item.value)) for item in referenced}
    for metric_name in statement.metric_names:
        if not any(pair[0] == metric_name for pair in pairs):
            raise FeedbackGroundingError(
                "metric_missing_from_statement",
                "every declared metric must appear beside a value in the statement",
            )
    for value in statement.numeric_values:
        if not any(_number_equal(value, pair[1]) for pair in pairs):
            raise FeedbackGroundingError(
                "number_missing_metric_binding",
                "every declared number must appear beside a canonical metric name",
            )
    for metric_name, value in pairs:
        if not any(
            metric_name == allowed_metric and _number_equal(value, allowed_value)
            for allowed_metric, allowed_value in referenced_pairs
        ):
            raise FeedbackGroundingError(
                "metric_value_contradiction",
                "a metric-value pair contradicted the referenced deterministic evidence",
            )


def validate_execution_feedback(
    feedback: ExecutionFeedback | Mapping[str, Any],
    evidence: ExecutionEvidencePackage | Mapping[str, Any],
) -> ExecutionFeedback:
    """Validate the complete grounding and product-safety contract."""
    package = (
        evidence
        if isinstance(evidence, ExecutionEvidencePackage)
        else ExecutionEvidencePackage.model_validate(evidence)
    )
    parsed = (
        feedback if isinstance(feedback, ExecutionFeedback) else ExecutionFeedback.model_validate(feedback)
    )
    if not package.released:
        raise FeedbackGroundingError(
            "hidden_not_released", "execution feedback is withheld until hidden results are released"
        )

    for statement in [*parsed.public_strengths, *parsed.hidden_failures]:
        _validate_statement(statement, package)

    all_text = _human_text(parsed)
    statement_text = {
        statement.statement for statement in [*parsed.public_strengths, *parsed.hidden_failures]
    }
    for text in all_text:
        if text not in statement_text:
            if _parsed_numbers(text):
                raise FeedbackGroundingError(
                    "quantitative_claim_outside_evidence_statement",
                    "all numeric claims must use a grounded EvidenceStatement",
                )
            if package.allowed_metric_names & set(_METRIC_TOKEN_PATTERN.findall(text.lower())):
                raise FeedbackGroundingError(
                    "metric_claim_outside_evidence_statement",
                    "all canonical metric claims must use a grounded EvidenceStatement",
                )
            if _RANK_ASSERTION_PATTERN.search(text.lower()):
                raise FeedbackGroundingError(
                    "rank_claim_outside_evidence_statement",
                    "all rank claims must use a grounded EvidenceStatement",
                )

    allowed_tokens = {
        *package.allowed_metric_names,
        *package.hidden_world_labels,
        package.policy_id,
        "hidden_results",
        "public_practice",
        "live_trading",
    }
    for text in all_text:
        for token in _METRIC_TOKEN_PATTERN.findall(text.lower()):
            if token not in allowed_tokens:
                raise FeedbackGroundingError(
                    "unknown_canonical_token",
                    "feedback used an unknown canonical metric or world token",
                )
        for token in _WORLD_TOKEN_PATTERN.findall(text.lower()):
            if "_" in token and token not in package.hidden_world_labels and token not in allowed_tokens:
                raise FeedbackGroundingError(
                    "unknown_hidden_world", "feedback referenced a world absent from released evidence"
                )

    _validate_safety_claims(all_text)
    limitation_text = " ".join(parsed.limitations).lower()
    if not any(term in limitation_text for term in ("deterministic", "synthetic", "fictional")):
        raise FeedbackGroundingError(
            "missing_synthetic_limitation", "feedback must disclose the deterministic synthetic limitation"
        )
    if not (
        _NEGATION_PATTERN.search(limitation_text)
        and any(
            term in limitation_text
            for term in ("profit", "investment", "production", "live-trading", "live trading")
        )
    ):
        raise FeedbackGroundingError(
            "missing_use_limitation",
            "feedback must disclaim profitability, investment, and production or live-trading conclusions",
        )
    return parsed


def _finite_metric(row: Mapping[str, Any], key: str) -> int | float | None:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return value


def build_execution_evidence(
    matrix: Mapping[str, Any],
    policy_id: str,
    *,
    released: bool,
    challenge_objective: str | None = None,
    policy_parameters: Mapping[str, Scalar] | None = None,
    event_ids: Sequence[str] = (),
    trade_ids: Sequence[str] = (),
    fill_ids: Sequence[str] = (),
    replay_step_ids: Sequence[str] = (),
    limitations: Sequence[str] = DEFAULT_LIMITATIONS,
) -> ExecutionEvidencePackage:
    """Project an evaluation matrix into a release-safe model evidence package."""
    rows = matrix.get("rows")
    if not isinstance(rows, list):
        raise ValueError("evaluation matrix must contain rows")
    row = next(
        (item for item in rows if isinstance(item, Mapping) and item.get("policy_id") == policy_id), None
    )
    if row is None:
        raise ValueError(f"evaluation matrix has no row for policy {policy_id!r}")
    raw_challenge = matrix.get("challenge")
    challenge: Mapping[str, Any] = raw_challenge if isinstance(raw_challenge, Mapping) else {}
    challenge_id = str(challenge.get("challenge_id") or "trade-the-shock")
    objective = challenge_objective or str(challenge.get("objective") or "Evaluate execution robustness.")

    discovered_parameters: dict[str, Scalar] = {}
    policies = challenge.get("policies") if isinstance(challenge, Mapping) else None
    if isinstance(policies, list):
        policy = next(
            (item for item in policies if isinstance(item, Mapping) and item.get("policy_id") == policy_id),
            None,
        )
        if policy is not None:
            for key, value in policy.items():
                if isinstance(value, (str, int, float, bool)):
                    discovered_parameters[str(key)] = value
    if policy_parameters:
        discovered_parameters.update(policy_parameters)

    items: list[EvidenceItem] = []

    def add_item(
        evidence_id: str,
        metric_name: str,
        value: int | float | None,
        phase: Literal["policy", "public", "hidden", "comparison"],
        context: str,
    ) -> None:
        if value is not None:
            items.append(
                EvidenceItem(
                    evidence_id=evidence_id,
                    metric_name=metric_name,
                    value=value,
                    phase=phase,
                    context=context,
                )
            )

    for key, value in sorted(discovered_parameters.items()):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        add_item(f"policy.{policy_id}.{key}", key, value, "policy", "submitted policy parameter")

    public_fields = {
        "public_score": "public_score",
        "public_rank": "public_rank",
        "public_shortfall_bps": "implementation_shortfall_bps",
        "public_completion_pct": "completion_pct",
    }
    for source, metric in public_fields.items():
        add_item(
            f"public.{policy_id}.{source}",
            metric,
            _finite_metric(row, source),
            "public",
            "released public-practice result",
        )

    raw_decomposition = row.get("score_decomposition")
    public_decomposition = row.get("public_score_decomposition")
    if not isinstance(public_decomposition, Mapping) and isinstance(raw_decomposition, Mapping):
        public_decomposition = raw_decomposition.get("public")
    if isinstance(public_decomposition, Mapping):
        for component, value in sorted(public_decomposition.items(), key=lambda item: str(item[0])):
            canonical = re.sub(r"[^a-z0-9]+", "_", str(component).lower()).strip("_")
            if not canonical:
                raise ValueError("public score decomposition contains an invalid component name")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("public score decomposition values must be numeric")
            add_item(
                f"public.{policy_id}.score_component.{canonical}",
                f"score_component_{canonical}",
                value,
                "public",
                "deterministic public-score decomposition",
            )

    hidden_labels: list[str] = []
    if released:
        hidden_fields = {
            "robustness_score": "robustness_score",
            "robustness_rank": "robustness_rank",
            "rank_movement": "rank_movement",
            "hidden_mean_shortfall_bps": "hidden_mean_shortfall_bps",
            "hidden_worst_shortfall_bps": "hidden_worst_shortfall_bps",
            "hidden_completion_pct": "hidden_completion_pct",
        }
        for source, metric in hidden_fields.items():
            add_item(
                f"hidden.{policy_id}.{source}",
                metric,
                _finite_metric(row, source),
                "hidden",
                "released aggregate hidden result",
            )

        hidden_decomposition = row.get("robustness_score_decomposition")
        if not isinstance(hidden_decomposition, Mapping) and isinstance(raw_decomposition, Mapping):
            nested_hidden = raw_decomposition.get("hidden")
            hidden_decomposition = nested_hidden if isinstance(nested_hidden, Mapping) else raw_decomposition
        if isinstance(hidden_decomposition, Mapping):
            for component, value in sorted(hidden_decomposition.items(), key=lambda item: str(item[0])):
                canonical = re.sub(r"[^a-z0-9]+", "_", str(component).lower()).strip("_")
                if not canonical:
                    raise ValueError("hidden score decomposition contains an invalid component name")
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("hidden score decomposition values must be numeric")
                add_item(
                    f"hidden.{policy_id}.score_component.{canonical}",
                    f"score_component_{canonical}",
                    value,
                    "hidden",
                    "deterministic robustness-score decomposition",
                )

        released_intents = row.get("released_intent_aggregates")
        if isinstance(released_intents, Mapping):
            for intent_id, raw_metrics in sorted(released_intents.items(), key=lambda item: str(item[0])):
                canonical_intent = re.sub(r"[^a-z0-9]+", "_", str(intent_id).lower()).strip("_")
                if not canonical_intent or not isinstance(raw_metrics, Mapping):
                    raise ValueError("released intent aggregates contain an invalid intent")
                hidden_labels.append(canonical_intent)
                for metric_name, value in sorted(raw_metrics.items(), key=lambda item: str(item[0])):
                    canonical_metric = re.sub(r"[^a-z0-9]+", "_", str(metric_name).lower()).strip("_")
                    if not canonical_metric:
                        raise ValueError("released intent aggregates contain an invalid metric")
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        raise ValueError("released intent aggregate values must be numeric")
                    add_item(
                        f"hidden.{policy_id}.intent.{canonical_intent}.{canonical_metric}",
                        canonical_metric,
                        value,
                        "hidden",
                        f"released aggregate for the {canonical_intent} educational intent",
                    )

        selected_world_metrics = {
            "implementation_shortfall_bps",
            "completion_pct",
            "max_participation_pct",
            "temporary_impact_bps",
            "remaining_inventory",
            "strategy_trade_count",
            "spread_paid_bps",
            "adverse_selection_bps",
        }
        world_results = row.get("world_results", [])
        if isinstance(world_results, list):
            for result in world_results:
                if not isinstance(result, Mapping):
                    continue
                variant = str(result.get("variant", ""))
                seed = result.get("seed")
                metrics = result.get("metrics")
                if not variant or not isinstance(seed, int) or not isinstance(metrics, Mapping):
                    continue
                hidden_labels.append(variant)
                for metric_name in sorted(selected_world_metrics):
                    add_item(
                        f"hidden.{policy_id}.{variant}.seed_{seed}.{metric_name}",
                        metric_name,
                        _finite_metric(metrics, metric_name),
                        "hidden",
                        f"released {variant} result for deterministic seed {seed}",
                    )

        robust_winner = min(
            (
                item
                for item in rows
                if isinstance(item, Mapping) and isinstance(item.get("robustness_rank"), int)
            ),
            key=lambda item: int(item["robustness_rank"]),
            default=None,
        )
        if robust_winner is not None:
            winner_id = str(robust_winner.get("policy_id"))
            for source in (
                "public_rank",
                "robustness_rank",
                "robustness_score",
                "hidden_mean_shortfall_bps",
                "hidden_worst_shortfall_bps",
                "hidden_completion_pct",
            ):
                add_item(
                    f"comparison.{winner_id}.{source}",
                    source,
                    _finite_metric(robust_winner, source),
                    "comparison",
                    "released robustness-winner comparison",
                )

    public_score = _finite_metric(row, "public_score")
    public_rank = _finite_metric(row, "public_rank")
    if public_score is None or public_rank is None:
        raise ValueError("matrix row must contain deterministic public_score and public_rank")
    outcome = DeterministicOutcome(
        policy_id=policy_id,
        public_score=float(public_score),
        public_rank=int(public_rank),
        robustness_score=float(row["robustness_score"])
        if released and _finite_metric(row, "robustness_score") is not None
        else None,
        robustness_rank=int(row["robustness_rank"])
        if released and _finite_metric(row, "robustness_rank") is not None
        else None,
        rank_movement=int(row["rank_movement"])
        if released and _finite_metric(row, "rank_movement") is not None
        else None,
    )
    return ExecutionEvidencePackage(
        challenge_id=challenge_id,
        challenge_objective=objective,
        policy_id=policy_id,
        policy_parameters=discovered_parameters,
        released=released,
        evidence_items=items,
        event_ids=list(event_ids),
        trade_ids=list(trade_ids),
        fill_ids=list(fill_ids),
        replay_step_ids=list(replay_step_ids),
        hidden_world_labels=sorted(set(hidden_labels)) if released else [],
        deterministic_outcome=outcome,
        limitations=list(limitations),
    )


def _items_by_metric(evidence: ExecutionEvidencePackage, metric_name: str) -> list[EvidenceItem]:
    return [item for item in evidence.evidence_items if item.metric_name == metric_name]


def deterministic_execution_feedback(evidence: ExecutionEvidencePackage) -> ExecutionFeedback:
    """Build a clearly labeled, source-bound explanation without model output."""
    public_rank = next(
        (
            item
            for item in _items_by_metric(evidence, "public_rank")
            if item.phase == "public" and evidence.policy_id in item.evidence_id
        ),
        None,
    )
    public_score = next(
        (
            item
            for item in _items_by_metric(evidence, "public_score")
            if item.phase == "public" and evidence.policy_id in item.evidence_id
        ),
        None,
    )
    public_strengths: list[EvidenceStatement] = []
    if public_rank is not None and public_score is not None:
        public_strengths.append(
            EvidenceStatement(
                statement=(
                    f"The released public_rank was {public_rank.value}, and public_score was "
                    f"{public_score.value}."
                ),
                evidence_ids=[public_rank.evidence_id, public_score.evidence_id],
                metric_names=["public_rank", "public_score"],
                numeric_values=[float(public_rank.value), float(public_score.value)],
            )
        )

    hidden_failures: list[EvidenceStatement] = []
    worst = next(
        (
            item
            for item in _items_by_metric(evidence, "hidden_worst_shortfall_bps")
            if item.phase == "hidden" and evidence.policy_id in item.evidence_id
        ),
        None,
    )
    if worst is not None:
        hidden_failures.append(
            EvidenceStatement(
                statement=f"The released hidden_worst_shortfall_bps was {worst.value}.",
                evidence_ids=[worst.evidence_id],
                metric_names=["hidden_worst_shortfall_bps"],
                numeric_values=[float(worst.value)],
            )
        )

    intent_latency = next(
        (
            item
            for item in _items_by_metric(evidence, "order_entry_latency_ms")
            if item.phase == "hidden" and ".intent.message_latency." in item.evidence_id
        ),
        None,
    )
    intent_shortfall = next(
        (
            item
            for item in _items_by_metric(evidence, "implementation_shortfall_bps")
            if item.phase == "hidden" and ".intent.message_latency." in item.evidence_id
        ),
        None,
    )
    if intent_latency is not None and intent_shortfall is not None:
        hidden_failures.append(
            EvidenceStatement(
                statement=(
                    "In the released message_latency intent, order_entry_latency_ms was "
                    f"{intent_latency.value} and implementation_shortfall_bps was "
                    f"{intent_shortfall.value}."
                ),
                evidence_ids=[intent_latency.evidence_id, intent_shortfall.evidence_id],
                metric_names=["order_entry_latency_ms", "implementation_shortfall_bps"],
                numeric_values=[float(intent_latency.value), float(intent_shortfall.value)],
            )
        )

    return ExecutionFeedback(
        summary="The deterministic evidence shows that visible practice performance and released robustness can answer different questions.",
        public_strengths=public_strengths,
        hidden_failures=hidden_failures,
        why_public_rank_changed=(
            "Public practice rewards the visible-world objective, while the released evaluation compares the same policy across protected conditions."
        ),
        why_robust_policy_survived=(
            "The robustness winner retained stronger measured behavior across the released evaluation matrix; deterministic rankings remain authoritative."
        ),
        recommended_policy_changes=[
            "Review participation, spread, latency, and completion controls against the cited replay evidence."
        ],
        next_experiments=[
            "Re-run one bounded policy change across the same released worlds and deterministic seeds."
        ],
        limitations=list(DEFAULT_LIMITATIONS),
    )


def _content_value(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, Mapping) else getattr(item, name, None)


def _response_refusal(response: Any) -> str | None:
    output = getattr(response, "output", None)
    if not isinstance(output, Sequence):
        return None
    for message in output:
        content = _content_value(message, "content")
        if not isinstance(content, Sequence):
            continue
        for item in content:
            if _content_value(item, "type") == "refusal":
                refusal = _content_value(item, "refusal")
                return str(refusal or "model refused the request")
    return None


def _fallback_result(
    evidence: ExecutionEvidencePackage,
    *,
    status: Literal["unavailable", "refused", "incomplete", "invalid"],
    message: str,
    model: str | None,
    reason: str,
) -> dict[str, Any]:
    feedback = deterministic_execution_feedback(evidence)
    return {
        "status": status,
        "mode": "deterministic_fallback",
        "generated_by": "deterministic_template",
        "gpt_analysis_available": False,
        "model": model,
        "message": message,
        "reason": reason,
        "feedback": feedback.model_dump(mode="json"),
        "evidence": evidence.model_payload(),
        "deterministic_outcome": evidence.deterministic_outcome.model_dump(mode="json"),
        "deterministic_outcome_hash": evidence.deterministic_outcome_hash,
        "scoring_authority": "deterministic_application_code",
    }


def generate_execution_feedback(
    evidence: ExecutionEvidencePackage | Mapping[str, Any],
    *,
    client: ResponsesClient | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Generate validated feedback or an explicit deterministic fallback."""
    package = (
        evidence
        if isinstance(evidence, ExecutionEvidencePackage)
        else ExecutionEvidencePackage.model_validate(evidence)
    )
    if not package.released:
        return {
            "status": "withheld",
            "mode": "withheld_until_release",
            "generated_by": "application_release_gate",
            "gpt_analysis_available": False,
            "model": None,
            "message": "Execution feedback is withheld until the instructor releases hidden results.",
            "evidence": package.model_payload(),
            "deterministic_outcome": package.deterministic_outcome.model_dump(mode="json"),
            "deterministic_outcome_hash": package.deterministic_outcome_hash,
            "scoring_authority": "deterministic_application_code",
        }

    selected_model = model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
    if client is None and not (api_key or os.getenv("OPENAI_API_KEY")):
        return _fallback_result(
            package,
            status="unavailable",
            message="GPT-5.6 analysis is unavailable because no OpenAI API key is configured.",
            model=None,
            reason="missing_api_key",
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
            {"role": "system", "content": EXECUTION_FEEDBACK_PROMPT},
            {
                "role": "user",
                "content": "Analyze this released verified evidence package:\n"
                + json.dumps(package.model_payload(), sort_keys=True, separators=(",", ":")),
            },
        ],
        text_format=ExecutionFeedback,
        max_output_tokens=4_000,
    )

    refusal = _response_refusal(response)
    if refusal is not None:
        return _fallback_result(
            package,
            status="refused",
            message="GPT-5.6 refused the analysis request; deterministic feedback is shown instead.",
            model=selected_model,
            reason="model_refusal",
        )
    if (
        getattr(response, "status", None) == "incomplete"
        or getattr(response, "incomplete_details", None) is not None
    ):
        return _fallback_result(
            package,
            status="incomplete",
            message="GPT-5.6 returned an incomplete response; deterministic feedback is shown instead.",
            model=selected_model,
            reason="incomplete_response",
        )

    output_parsed = getattr(response, "output_parsed", None)
    if output_parsed is None:
        return _fallback_result(
            package,
            status="incomplete",
            message="GPT-5.6 returned no complete structured output; deterministic feedback is shown instead.",
            model=selected_model,
            reason="missing_structured_output",
        )
    try:
        parsed = (
            output_parsed
            if isinstance(output_parsed, ExecutionFeedback)
            else ExecutionFeedback.model_validate(output_parsed)
        )
    except ValidationError:
        return _fallback_result(
            package,
            status="incomplete",
            message="GPT-5.6 returned an incomplete structured response; deterministic feedback is shown instead.",
            model=selected_model,
            reason="invalid_structured_output",
        )
    try:
        validated = validate_execution_feedback(parsed, package)
    except FeedbackGroundingError as exc:
        return _fallback_result(
            package,
            status="invalid",
            message="GPT-5.6 output failed evidence validation; deterministic feedback is shown instead.",
            model=selected_model,
            reason=exc.code,
        )

    return {
        "status": "complete",
        "mode": "gpt-5.6",
        "generated_by": "openai_responses_api_structured_output",
        "gpt_analysis_available": True,
        "model": selected_model,
        "feedback": validated.model_dump(mode="json"),
        "evidence": package.model_payload(),
        "deterministic_outcome": package.deterministic_outcome.model_dump(mode="json"),
        "deterministic_outcome_hash": package.deterministic_outcome_hash,
        "scoring_authority": "deterministic_application_code",
    }
