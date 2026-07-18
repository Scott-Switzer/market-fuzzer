"""Enterprise synthetic-market registry contracts.

These records describe reproducible market-world inputs. They do not execute a
simulation or assign outcomes; the deterministic compiler remains authoritative.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

WORLD_SCHEMA_VERSION = "1.0"
SCENARIO_SCHEMA_VERSION = "1.0"
INTERVENTION_TYPES = (
    "liquidity_withdrawal",
    "volatility_shock",
    "latency_shock",
    "crowding",
    "adverse_selection",
    "completion_pressure",
)


class SyntheticWorldCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=20, max_length=2_000)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    asset_universe: list[str] = Field(min_length=1, max_length=100)
    venue: Literal["continuous_double_auction"] = "continuous_double_auction"
    agent_ecology: list[str] = Field(min_length=1, max_length=32)
    calibration_ref: str | None = Field(default=None, max_length=200)
    intended_use: Literal["execution_stress_testing", "strategy_research", "training"] = (
        "execution_stress_testing"
    )
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("asset_universe", "agent_ecology", "tags")
    @classmethod
    def unique_values(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 120 for value in cleaned):
            raise ValueError("list values must be non-empty and at most 120 characters")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("list values must be unique")
        return cleaned


class ScenarioIntervention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intervention_type: Literal[
        "liquidity_withdrawal",
        "volatility_shock",
        "latency_shock",
        "crowding",
        "adverse_selection",
        "completion_pressure",
    ]
    severity: Literal["low", "moderate", "high"] = "moderate"
    start_step: int = Field(ge=0, le=10_000)
    duration_steps: int = Field(ge=1, le=10_000)
    rationale: str = Field(min_length=10, max_length=700)


class ScenarioPackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=20, max_length=2_000)
    base_world_id: str = Field(min_length=3, max_length=100)
    interventions: list[ScenarioIntervention] = Field(min_length=1, max_length=12)
    intended_question: str = Field(min_length=20, max_length=1_000)


def new_registry_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:16]}"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
