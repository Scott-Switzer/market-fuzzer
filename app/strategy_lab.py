"""Enterprise strategy and stress-experiment contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrategyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=20, max_length=2_000)
    strategy_type: Literal["arena_policy", "external_adapter"] = "arena_policy"
    builtin_policy_id: Literal["twap", "aggressive_pov", "guarded_pov", "completion_first"] | None = None
    version_label: str = Field(default="1.0.0", pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    intended_use: Literal["execution_stress_testing", "strategy_research"] = "execution_stress_testing"


class StressExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=160)
    strategy_ids: list[str] = Field(min_length=1, max_length=32)
    scenario_pack_id: str = Field(min_length=3, max_length=100)
    seeds: list[int] = Field(default_factory=lambda: [41, 42], min_length=1, max_length=32)
