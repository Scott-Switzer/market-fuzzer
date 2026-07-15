"""Optional, evidence-grounded GPT-5.6 explanations for deterministic failures."""

from __future__ import annotations

import os
import re
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class AnalystHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=1_000)
    evidence_references: list[str] = Field(min_length=1, max_length=8)


class FailureAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=1_000)
    failure_mechanism: str = Field(min_length=1, max_length=2_000)
    evidence_references: list[str] = Field(min_length=1, max_length=16)
    why_the_neighbor_passes: str = Field(min_length=1, max_length=1_500)
    why_the_correction_works: str = Field(min_length=1, max_length=1_500)
    recommended_regression_assertions: list[str] = Field(min_length=1, max_length=8)
    additional_test_hypotheses: list[AnalystHypothesis] = Field(default_factory=list, max_length=6)
    limitations: list[str] = Field(min_length=1, max_length=8)


class AnalystClient(Protocol):
    responses: Any


ANALYST_SYSTEM_PROMPT = """You are the Market Fuzzer Failure Analyst.
Explain only the deterministic evidence supplied by the application. Never invent values,
market observations, confidence, causality, or production implications. Every evidence reference
must be copied exactly from the allowed evidence-reference list. The product is a compact
deterministic synthetic market test harness, not a full exchange or live-trading validator.
Return concise structured output. Make limitations explicit. GPT must not decide PASS/FAIL;
the application already computed those verdicts."""


def _numeric_values(value: Any) -> set[float]:
    if isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        return {float(value)}
    if isinstance(value, dict):
        result: set[float] = set()
        for item in value.values():
            result.update(_numeric_values(item))
        return result
    if isinstance(value, list):
        result = set()
        for item in value:
            result.update(_numeric_values(item))
        return result
    return set()


def _text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _text_values(child)]
    if isinstance(value, list):
        return [item for child in value for item in _text_values(child)]
    return []


def evidence_package(failure: dict[str, Any]) -> dict[str, Any]:
    """Project a failure into the small, verified package sent to the model."""
    property_row = failure["violated_property"]
    minimized = failure["minimized"]
    neighbor = failure["passing_neighbor"]
    runs = failure["runs"]
    corrected_runs = failure.get("corrected_runs", [])
    references = {
        "property.participation",
        "scenario.hash",
        "scenario.minimized",
        "scenario.passing_neighbor",
        "reproduction.seeds",
        "reproduction.failure_rate",
        "minimization.severity",
        "minimization.trace",
    }
    references.update(f"run.{run['seed']}.metrics.participation" for run in runs)
    references.update(f"run.{run['seed']}.timeline" for run in runs)
    references.update(f"corrected.{run['seed']}.metrics.participation" for run in corrected_runs)
    return {
        "strategy": {
            "id": failure.get("strategy", {}).get("id", "pov_fragile"),
            "type": failure.get("strategy", {}).get("type", "POV"),
            "version": failure.get("strategy", {}).get("version", "built-in-1"),
        },
        "property": {
            "id": property_row["id"],
            "operator": property_row["operator"],
            "threshold": property_row["threshold"],
            "observed": property_row["observed"],
            "margin": property_row["margin"],
            "units": property_row["units"],
            "first_violation_time": property_row.get("first_violation_time"),
        },
        "scenario": {
            "hash": failure["scenario_hash"],
            "minimized": minimized,
            "passing_neighbor": neighbor,
            "neighbor_delta": {
                key: [minimized.get(key), neighbor.get(key)]
                for key in sorted(set(minimized) | set(neighbor))
                if minimized.get(key) != neighbor.get(key)
            },
        },
        "reproduction": failure["reproduction"],
        "minimization": {
            "severity": failure["severity"],
            "trace": failure["minimization_trace"],
        },
        "runs": [
            {
                "seed": run["seed"],
                "metrics": run["metrics"],
                "participation_property": next(
                    row for row in run["properties"] if row["id"] == "participation"
                ),
            }
            for run in runs
        ],
        "corrected_runs": [
            {"seed": run["seed"], "metrics": run["metrics"], "passed": run["passed"]}
            for run in corrected_runs
        ],
        "allowed_evidence_references": sorted(references),
        "limitations": [
            "Results describe software behavior inside the configured compact deterministic synthetic harness.",
            "The harness is not institutional calibration or a production capacity model.",
            "The analysis cannot establish profitability, live-trading safety, or regulatory compliance.",
        ],
    }


def _validate_grounding(analysis: FailureAnalysis, evidence: dict[str, Any]) -> FailureAnalysis:
    allowed_refs = set(evidence["allowed_evidence_references"])
    all_refs = list(analysis.evidence_references) + [
        ref for hypothesis in analysis.additional_test_hypotheses for ref in hypothesis.evidence_references
    ]
    unknown = sorted(set(all_refs) - allowed_refs)
    if unknown:
        raise ValueError(f"analysis referenced unknown evidence: {', '.join(unknown)}")
    numeric_allowed = _numeric_values(evidence) | {5.6}
    numeric_pattern = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?")
    for text in _text_values(analysis.model_dump(mode="python")):
        for token in numeric_pattern.findall(text):
            if float(token) not in numeric_allowed:
                raise ValueError(f"analysis contains an unsupported numeric claim: {token}")
    if not any("synthetic" in limitation.lower() for limitation in analysis.limitations):
        raise ValueError("analysis must state the synthetic-environment limitation")
    return analysis


def deterministic_analysis(failure: dict[str, Any]) -> dict[str, Any]:
    evidence = evidence_package(failure)
    delta = evidence["scenario"]["neighbor_delta"]
    return {
        "status": "unavailable",
        "mode": "deterministic_fallback",
        "message": "GPT-5.6 analysis unavailable in no-key mode.",
        "analysis": {
            "summary": "The fragile POV exceeds its participation cap in the minimized synthetic scenario.",
            "failure_mechanism": "The strategy sizes from delayed observed volume while forced flow contracts executable volume; its pending-order accounting does not protect the realized participation cap.",
            "evidence_references": [
                "property.participation",
                "scenario.minimized",
                "minimization.severity",
                "reproduction.seeds",
            ],
            "why_the_neighbor_passes": f"The verified neighbor changes {', '.join(delta) or 'the scenario'} and is evaluated independently across the recorded seeds.",
            "why_the_correction_works": "The corrected POV includes pending quantity in its budget and applies a fill-time participation guard; its deterministic run remains the verdict authority.",
            "recommended_regression_assertions": [
                "Assert the participation property remains enabled.",
                "Replay the minimized scenario with the stored seed list.",
                "Require the fragile strategy to fail and the corrected strategy to pass.",
            ],
            "additional_test_hypotheses": [],
            "limitations": evidence["limitations"],
        },
        "evidence": evidence,
    }


def analyze_failure(
    failure: dict[str, Any],
    *,
    client: AnalystClient | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    evidence = evidence_package(failure)
    if client is None and not (api_key or os.getenv("OPENAI_API_KEY")):
        return deterministic_analysis(failure)
    active_client: Any = client
    if active_client is None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("OpenAI SDK is not installed; use the no-key fallback") from exc
        active_client = OpenAI(api_key=api_key, timeout=30.0, max_retries=2)
    selected_model = model or os.getenv("OPENAI_MODEL") or "gpt-5.6"
    response = active_client.responses.parse(
        model=selected_model,
        input=[
            {"role": "system", "content": ANALYST_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Analyze this verified evidence package. Use only the allowed evidence references and numeric values:\n"
                + str(evidence),
            },
        ],
        text_format=FailureAnalysis,
        max_output_tokens=4_000,
    )
    parsed = response.output_parsed
    if parsed is None:
        raise ValueError("GPT-5.6 returned no structured failure analysis")
    if isinstance(parsed, dict):
        parsed = FailureAnalysis.model_validate(parsed)
    if not isinstance(parsed, FailureAnalysis):
        raise ValueError("GPT-5.6 returned an unexpected structured output type")
    validated = _validate_grounding(parsed, evidence)
    return {
        "status": "complete",
        "mode": "gpt-5.6",
        "model": selected_model,
        "analysis": validated.model_dump(mode="json"),
        "evidence": evidence,
    }
