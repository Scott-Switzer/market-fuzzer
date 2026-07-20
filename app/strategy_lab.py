"""Enterprise strategy and stress-experiment contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExternalAdapterContract(BaseModel):
    """Bounded adapter metadata; executable strategy code stays outside the API process."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    adapter_id: Literal["declarative_in_process_v1", "http_json_v1", "container_jsonl_v1"]
    adapter_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    policy_id: Literal["twap", "aggressive_pov", "guarded_pov", "completion_first"]
    input_observation_schema: Literal["market_observation_v1", "market_observation_v2"]
    output_action_schema: Literal["execution_action_v1", "execution_action_v2"]
    timeout_ms: int = Field(ge=1, le=1_000)
    error_policy: Literal["fail_cell", "reject_action"] = "fail_cell"
    endpoint_url: str | None = Field(default=None, max_length=500)
    auth_env_var: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{2,80}$")
    image_digest: str | None = Field(default=None, max_length=500)
    command: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _bounded_contract(self) -> ExternalAdapterContract:
        if self.adapter_id == "http_json_v1" and not self.endpoint_url:
            raise ValueError("http_json_v1 adapters require an endpoint_url")
        if self.adapter_id == "declarative_in_process_v1" and self.endpoint_url:
            raise ValueError("in-process adapters cannot include an endpoint_url")
        if self.adapter_id == "container_jsonl_v1" and (not self.image_digest or not self.command):
            raise ValueError("container_jsonl_v1 adapters require image_digest and command")
        if self.adapter_id == "container_jsonl_v1" and (self.endpoint_url or self.auth_env_var):
            raise ValueError("container adapters cannot include HTTP endpoint metadata")
        if (self.input_observation_schema.endswith("v2")) != (self.output_action_schema.endswith("v2")):
            raise ValueError("strategy observation and action schemas must use the same protocol version")
        if self.adapter_id != "container_jsonl_v1" and self.input_observation_schema.endswith("v2"):
            raise ValueError("strategy protocol V2 requires the isolated container adapter")
        return self


class StrategyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=20, max_length=2_000)
    strategy_type: Literal["arena_policy", "external_adapter"] = "arena_policy"
    builtin_policy_id: Literal["twap", "aggressive_pov", "guarded_pov", "completion_first"] | None = None
    version_label: str = Field(default="1.0.0", pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    intended_use: Literal["execution_stress_testing", "strategy_research"] = "execution_stress_testing"
    external_adapter: ExternalAdapterContract | None = None

    @model_validator(mode="after")
    def _valid_strategy(self) -> StrategyCreate:
        if self.strategy_type == "external_adapter" and self.external_adapter is None:
            raise ValueError("external_adapter strategies require a bounded adapter contract")
        if self.strategy_type == "arena_policy" and self.external_adapter is not None:
            raise ValueError("arena_policy strategies cannot include an external adapter contract")
        return self


class StressExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=160)
    strategy_ids: list[str] = Field(min_length=1, max_length=32)
    scenario_pack_id: str = Field(min_length=3, max_length=100)
    seeds: list[int] = Field(default_factory=lambda: [41, 42], min_length=1, max_length=32)
