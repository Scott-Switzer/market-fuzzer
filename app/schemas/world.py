from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ClockSpec(StrictModel):
    start: datetime = Field(default_factory=lambda: datetime(2026, 1, 5, 14, 30, tzinfo=UTC))
    end: datetime = Field(default_factory=lambda: datetime(2026, 1, 5, 15, 30, tzinfo=UTC))
    step_seconds: int = Field(default=30, ge=1, le=300)
    step_or_event_mode: Literal["step", "event"] = "step"

    @model_validator(mode="after")
    def ordered(self) -> ClockSpec:
        if self.end <= self.start:
            raise ValueError("clock.end must be after clock.start")
        if (self.end - self.start).total_seconds() / self.step_seconds > 3_600:
            raise ValueError("clock contains more than 3,600 steps")
        return self

    @property
    def steps(self) -> int:
        return int((self.end - self.start).total_seconds() // self.step_seconds)


class MacroSpec(StrictModel):
    growth_regime: Literal["contraction", "slow", "trend", "expansion"] = "trend"
    rate_shock_bps: int = Field(default=0, ge=-500, le=500)
    volatility_regime: Literal["low", "normal", "elevated", "crisis"] = "normal"
    risk_aversion: float = Field(default=1.0, ge=0.1, le=5.0)
    common_factor_strength: float = Field(default=0.35, ge=0.0, le=1.0)


class AssetSpec(StrictModel):
    ticker: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,5}$")
    company_name: str = Field(min_length=3, max_length=80)
    sector: str = Field(min_length=2, max_length=50)
    initial_price_ticks: int = Field(gt=0, le=10_000_000)
    shares_outstanding: int = Field(gt=0, le=10_000_000_000)
    initial_fundamental_value_ticks: int = Field(gt=0, le=10_000_000)
    macro_beta: float = Field(ge=-3.0, le=5.0)
    idiosyncratic_volatility: float = Field(gt=0.0, le=0.2)
    liquidity_profile: Literal["deep", "normal", "thin"] = "normal"
    event_sensitivity: float = Field(default=1.0, ge=0.0, le=3.0)
    mean_reversion: float = Field(default=0.02, ge=0.0, le=0.5)


class ExchangeSpec(StrictModel):
    mechanism: Literal["continuous_double_auction"] = "continuous_double_auction"
    price_time_priority: Literal[True] = True
    tick_size_cents: int = Field(default=1, gt=0, le=100)
    lot_size: int = Field(default=1, gt=0, le=10_000)
    maker_fee_bps: float = Field(default=-0.1, ge=-5, le=20)
    taker_fee_bps: float = Field(default=0.3, ge=0, le=50)
    latency_profile: Literal["low", "normal", "high"] = "normal"
    circuit_breaker_pct: float = Field(default=10.0, gt=0.1, le=50.0)
    halt_steps: int = Field(default=4, ge=1, le=100)
    book_depth_levels: int = Field(default=5, ge=1, le=20)
    baseline_depth: int = Field(default=600, ge=10, le=1_000_000)


class AgentPopulation(StrictModel):
    type: Literal[
        "market_maker",
        "fundamental",
        "momentum",
        "mean_reversion",
        "noise",
        "forced_liquidator",
        "execution",
    ]
    count: int = Field(default=1, ge=1, le=500)
    capital_cents: int = Field(default=10_000_000_00, gt=0)
    latency_ms: int = Field(default=10, ge=0, le=10_000)
    risk_limit_shares: int = Field(default=100_000, gt=0)
    parameters: dict[str, float | int | str | bool] = Field(default_factory=dict)


class AgentsSpec(StrictModel):
    populations: list[AgentPopulation]

    @model_validator(mode="after")
    def required_types(self) -> AgentsSpec:
        present = {population.type for population in self.populations}
        required = {
            "market_maker",
            "fundamental",
            "momentum",
            "mean_reversion",
            "noise",
            "forced_liquidator",
            "execution",
        }
        missing = required - present
        if missing:
            raise ValueError(f"agents.populations missing required types: {sorted(missing)}")
        return self


class EventSpec(StrictModel):
    event_id: str = Field(min_length=2, max_length=80)
    simulation_step: int = Field(ge=0)
    scope: Literal["market", "asset"]
    asset: str | None = None
    type: Literal["earnings", "guidance", "liquidity_withdrawal", "forced_liquidation", "macro"]
    public_or_private: Literal["public", "private"] = "public"
    fundamental_effect_pct: float = Field(default=0.0, ge=-50.0, le=50.0)
    liquidity_effect: float = Field(default=1.0, ge=0.05, le=3.0)
    narrative: str = Field(min_length=3, max_length=500)


class ParentOrderSpec(StrictModel):
    side: Literal["buy", "sell"] = "buy"
    quantity: int = Field(gt=0, le=5_000_000)
    limit_price_ticks: int | None = Field(default=None, gt=0)


class ExperimentSpec(StrictModel):
    strategy: Literal["twap", "pov"] = "twap"
    parent_order: ParentOrderSpec
    participation_rate: float = Field(default=0.08, gt=0.0, le=0.5)
    urgency: float = Field(default=0.5, ge=0.0, le=1.0)
    latency_ms: int = Field(default=5, ge=0, le=10_000)
    target_asset: str
    counterfactual_mutations: list[str] = Field(
        default_factory=lambda: [
            "normal",
            "liquidity_withdrawal",
            "earnings_shock",
            "crowded_unwind",
        ]
    )
    repetitions: int = Field(default=2, ge=1, le=20)


class WorldSpec(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    world_id: str = Field(min_length=3, max_length=80)
    seed: int = Field(ge=0, le=2_147_483_647)
    clock: ClockSpec
    macro: MacroSpec
    assets: list[AssetSpec] = Field(min_length=3, max_length=20)
    exchange: ExchangeSpec
    agents: AgentsSpec
    events: list[EventSpec] = Field(default_factory=list, max_length=100)
    experiment: ExperimentSpec

    @model_validator(mode="after")
    def cross_fields(self) -> WorldSpec:
        tickers = [asset.ticker for asset in self.assets]
        if len(tickers) != len(set(tickers)):
            raise ValueError("assets must have unique tickers")
        if self.experiment.target_asset not in tickers:
            raise ValueError("experiment.target_asset must reference an asset ticker")
        for event in self.events:
            if event.simulation_step >= self.clock.steps:
                raise ValueError(f"event {event.event_id} is outside the simulation clock")
            if event.scope == "asset" and event.asset not in tickers:
                raise ValueError(f"event {event.event_id} must reference a known asset")
        if self.experiment.parent_order.quantity % self.exchange.lot_size:
            raise ValueError("parent order quantity must be a multiple of exchange.lot_size")
        return self

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def specification_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False)


class CompileResult(StrictModel):
    spec: WorldSpec
    compiler_mode: Literal["offline", "gpt"]
    model: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def spec_hash(self) -> str:
        return self.spec.specification_hash()


def demo_clock(steps: int = 120, step_seconds: int = 30) -> ClockSpec:
    start = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    return ClockSpec(
        start=start, end=start + timedelta(seconds=steps * step_seconds), step_seconds=step_seconds
    )
