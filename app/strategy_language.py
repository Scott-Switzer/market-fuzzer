"""Deterministic plain-English strategy brief compiler.

This is intentionally a proposal compiler, not a code execution feature.  A
brief is mapped to an allow-listed policy and must still be registered before
it can run in the exchange.
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.strategy_lab import StrategyCreate

PolicyId = Literal["twap", "aggressive_pov", "guarded_pov", "completion_first"]


class StrategyBriefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brief: str = Field(min_length=20, max_length=2_000)


def _checksum(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


def compile_strategy_brief(brief: str) -> dict:
    """Compile bounded strategy language into a reviewable policy proposal."""

    normalized = re.sub(r"\s+", " ", brief.strip().lower())
    matches: list[str] = []
    if re.search(r"\b(twap|time[- ]weighted|evenly|slice)\b", normalized):
        matches.append("time_sliced_execution")
    if re.search(r"\b(pov|participat\w*|volume[- ]weighted|trade with volume|market volume)\b", normalized):
        matches.append("volume_participation")
    if re.search(r"\b(urgent|completion|finish|complete|deadline)\b", normalized):
        matches.append("completion_priority")
    if re.search(r"\b(spread|liquidity|latency|halt|protect|defensive|pause)\b", normalized):
        matches.append("stress_guardrails")

    policy_id: PolicyId
    if "time_sliced_execution" in matches:
        policy_id = "twap"
        rationale = "The brief requests time-sliced execution; the deterministic TWAP policy is the closest bounded policy."
    elif "volume_participation" in matches and "stress_guardrails" in matches:
        policy_id = "guarded_pov"
        rationale = "The brief requests participation with stress protection; the deterministic guarded POV policy is the closest bounded policy."
    elif "completion_priority" in matches:
        policy_id = "completion_first"
        rationale = "The brief prioritizes completion; the deterministic completion-first policy is the closest bounded policy."
    elif "volume_participation" in matches:
        policy_id = "guarded_pov"
        rationale = "The brief requests volume participation; the deterministic guarded POV policy is the closest bounded policy."
    else:
        policy_id = "guarded_pov"
        rationale = "The brief did not match a stronger bounded intent, so the compiler proposes guarded POV and requires confirmation."

    ambiguities = [] if matches else ["No explicit bounded execution intent was detected."]
    proposal = StrategyCreate(
        name=f"Brief proposal: {policy_id}",
        description=(
            f"Compiled from a plain-English strategy brief. {rationale} "
            "The proposal is evaluated only inside the controlled synthetic exchange."
        ),
        strategy_type="arena_policy",
        builtin_policy_id=policy_id,
    )
    return {
        "compiler_version": "strategy_brief_v1",
        "input_checksum": f"sha256:{_checksum(normalized)}",
        "normalized_brief": normalized,
        "matched_intents": matches,
        "ambiguities": ambiguities,
        "requires_confirmation": True,
        "claim_boundary": "bounded_policy_proposal; no prose or customer code executes in the API process",
        "proposal": proposal.model_dump(mode="json"),
    }
