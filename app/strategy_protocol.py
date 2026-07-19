"""Versioned observation/action protocol for customer-owned strategy adapters."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrategyObservationV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    session_id: str = Field(min_length=3, max_length=160)
    step: int = Field(ge=0)
    symbol: str = Field(min_length=1, max_length=32)
    side: Literal["buy", "sell"]
    mid_ticks: int = Field(gt=0)
    best_bid_ticks: int | None = Field(default=None, gt=0)
    best_ask_ticks: int | None = Field(default=None, gt=0)
    spread_bps: float = Field(ge=0.0)
    observed_volume: int = Field(ge=0)
    inventory: int
    remaining_quantity: int = Field(ge=0)
    exchange_latency_profile: Literal["low", "normal", "high"]
    intervention_active: bool


class StrategyActionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    action_type: Literal["hold", "market", "limit"]
    side: Literal["buy", "sell"] | None = None
    quantity: int = Field(default=0, ge=0)
    limit_price_ticks: int | None = Field(default=None, gt=0)
    rationale_code: str = Field(default="adapter_decision", min_length=3, max_length=80)

    @model_validator(mode="after")
    def valid_action(self) -> StrategyActionV1:
        if self.action_type == "hold" and (self.side is not None or self.quantity != 0):
            raise ValueError("hold actions cannot include side or quantity")
        if self.action_type != "hold" and (self.side is None or self.quantity < 1):
            raise ValueError("market and limit actions require side and positive quantity")
        if self.action_type != "limit" and self.limit_price_ticks is not None:
            raise ValueError("only limit actions may include a limit price")
        return self
