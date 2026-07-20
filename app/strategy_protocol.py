"""Versioned observation/action protocol for customer-owned strategy adapters."""

from __future__ import annotations

from typing import Literal, TypeAlias

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


class StrategyOpenOrderV2(BaseModel):
    """One of the strategy's own currently resting orders; queue identity stays private."""

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(min_length=1, max_length=160)
    side: Literal["buy", "sell"]
    remaining_quantity: int = Field(gt=0)
    limit_price_ticks: int = Field(gt=0)


class StrategyObservationV2(BaseModel):
    """V2 adds only the strategy's visible live-order lifecycle state."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"] = "2.0"
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
    open_orders: tuple[StrategyOpenOrderV2, ...] = ()


class StrategyActionV2(BaseModel):
    """Versioned lifecycle commands for the isolated V2 exchange adapter."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"] = "2.0"
    action_type: Literal["hold", "submit", "cancel", "replace"]
    side: Literal["buy", "sell"] | None = None
    order_type: Literal["market", "limit"] | None = None
    quantity: int = Field(default=0, ge=0)
    limit_price_ticks: int | None = Field(default=None, gt=0)
    order_id: str | None = Field(default=None, min_length=1, max_length=160)
    rationale_code: str = Field(default="adapter_decision", min_length=3, max_length=80)

    @model_validator(mode="after")
    def valid_action(self) -> StrategyActionV2:
        if self.action_type == "hold":
            if (
                any(
                    value is not None
                    for value in (self.side, self.order_type, self.limit_price_ticks, self.order_id)
                )
                or self.quantity
            ):
                raise ValueError("hold actions cannot include command fields")
            return self
        if self.action_type == "submit":
            if self.side is None or self.order_type is None or self.quantity < 1 or self.order_id is not None:
                raise ValueError("submit actions require side, order type, and positive quantity")
            if self.order_type == "limit" and self.limit_price_ticks is None:
                raise ValueError("limit submissions require limit_price_ticks")
            if self.order_type == "market" and self.limit_price_ticks is not None:
                raise ValueError("market submissions cannot include limit_price_ticks")
            return self
        if self.action_type == "cancel":
            if (
                self.order_id is None
                or self.quantity
                or any(value is not None for value in (self.side, self.order_type, self.limit_price_ticks))
            ):
                raise ValueError("cancel actions require only order_id")
            return self
        if (
            self.order_id is None
            or self.quantity < 1
            or self.limit_price_ticks is None
            or self.side is not None
            or self.order_type is not None
        ):
            raise ValueError("replace actions require order_id, quantity, and limit_price_ticks only")
        return self


StrategyObservation: TypeAlias = StrategyObservationV1 | StrategyObservationV2
StrategyAction: TypeAlias = StrategyActionV1 | StrategyActionV2


def parse_strategy_observation(value: dict) -> StrategyObservation:
    """Parse an explicit protocol version; missing versions retain V1 compatibility."""
    if value.get("schema_version", "1.0") == "2.0":
        return StrategyObservationV2.model_validate(value)
    return StrategyObservationV1.model_validate(value)


def parse_strategy_action(value: dict) -> StrategyAction:
    """Parse an explicit response protocol version; missing versions retain V1 compatibility."""
    if value.get("schema_version", "1.0") == "2.0":
        return StrategyActionV2.model_validate(value)
    return StrategyActionV1.model_validate(value)


def failure_hold_action(observation: StrategyObservation) -> dict:
    """Return a protocol-matched fail-closed hold response."""
    if isinstance(observation, StrategyObservationV2):
        return StrategyActionV2(action_type="hold", rationale_code="isolated_runner_failure").model_dump(
            mode="json"
        )
    return StrategyActionV1(action_type="hold", rationale_code="isolated_runner_failure").model_dump(
        mode="json"
    )
