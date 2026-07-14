from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.exchange import Order, OrderType, Side
from app.schemas import AgentPopulation, WorldSpec


@dataclass
class AgentContext:
    step: int
    symbol: str
    fundamental_ticks: int
    mid_ticks: int
    recent_prices: list[int]
    observed_volume: int
    liquidity_multiplier: float


@dataclass
class BaseAgent:
    agent_id: str
    agent_type: str
    latency_ms: int
    capital_cents: int
    risk_limit_shares: int
    parameters: dict[str, float | int | str | bool]
    order_counter: int = 0
    resting_order_ids: set[str] = field(default_factory=set)

    def next_id(self) -> str:
        self.order_counter += 1
        return f"{self.agent_id}-O{self.order_counter:07d}"

    def market(self, context: AgentContext, side: Side, quantity: int) -> Order | None:
        if quantity <= 0:
            return None
        return Order(self.next_id(), self.agent_id, context.symbol, side, OrderType.MARKET, quantity, context.step)

    def decide(self, context: AgentContext, rng: random.Random, inventory: int) -> list[Order]:
        return []


class MarketMaker(BaseAgent):
    def decide(self, context: AgentContext, rng: random.Random, inventory: int) -> list[Order]:
        spread = int(self.parameters.get("spread_ticks", 4))
        levels = int(self.parameters.get("levels", 5))
        skew = float(self.parameters.get("inventory_skew", 0.002))
        reservation = round(0.7 * context.mid_ticks + 0.3 * context.fundamental_ticks - inventory * skew)
        size = max(10, int(160 * context.liquidity_multiplier * max(0.2, 1 - abs(inventory) / self.risk_limit_shares)))
        orders: list[Order] = []
        for level in range(1, levels + 1):
            bid = max(1, reservation - spread * level)
            ask = reservation + spread * level
            quantity = max(10, size // level)
            orders.extend([
                Order(self.next_id(), self.agent_id, context.symbol, Side.BUY, OrderType.LIMIT, quantity, context.step, bid),
                Order(self.next_id(), self.agent_id, context.symbol, Side.SELL, OrderType.LIMIT, quantity, context.step, ask),
            ])
        return orders


class FundamentalTrader(BaseAgent):
    def decide(self, context: AgentContext, rng: random.Random, inventory: int) -> list[Order]:
        gap = context.fundamental_ticks / context.mid_ticks - 1
        if abs(gap) < 0.0015 or abs(inventory) >= self.risk_limit_shares:
            return []
        quantity = min(80, max(10, int(abs(gap) * 5_000)))
        order = self.market(context, Side.BUY if gap > 0 else Side.SELL, quantity)
        return [order] if order else []


class MomentumTrader(BaseAgent):
    def decide(self, context: AgentContext, rng: random.Random, inventory: int) -> list[Order]:
        lookback = int(self.parameters.get("lookback", 4))
        if len(context.recent_prices) <= lookback or abs(inventory) >= self.risk_limit_shares:
            return []
        change = context.recent_prices[-1] - context.recent_prices[-lookback]
        if change == 0:
            return []
        crowding = float(self.parameters.get("crowding", 1.0))
        order = self.market(context, Side.BUY if change > 0 else Side.SELL, max(10, int(20 * crowding)))
        return [order] if order else []


class MeanReversionTrader(BaseAgent):
    def decide(self, context: AgentContext, rng: random.Random, inventory: int) -> list[Order]:
        if len(context.recent_prices) < 6 or abs(inventory) >= self.risk_limit_shares:
            return []
        mean = sum(context.recent_prices[-6:]) / 6
        gap = context.mid_ticks / mean - 1
        if abs(gap) < 0.001:
            return []
        order = self.market(context, Side.SELL if gap > 0 else Side.BUY, 20)
        return [order] if order else []


class NoiseTrader(BaseAgent):
    def decide(self, context: AgentContext, rng: random.Random, inventory: int) -> list[Order]:
        if rng.random() > 0.22 or abs(inventory) >= self.risk_limit_shares:
            return []
        order = self.market(context, Side.BUY if rng.random() > 0.5 else Side.SELL, rng.choice((10, 20, 30, 40)))
        return [order] if order else []


class ForcedLiquidator(BaseAgent):
    def decide(self, context: AgentContext, rng: random.Random, inventory: int) -> list[Order]:
        start = int(self.parameters.get("start_step", 10_000))
        total = int(self.parameters.get("total_quantity", 0))
        if context.step < start or self.order_counter * 200 >= total or context.symbol != "NOVA":
            return []
        order = self.market(context, Side.SELL, min(200, total - self.order_counter * 200))
        return [order] if order else []


class ExecutionAgent(BaseAgent):
    target_quantity: int = 0
    executed_quantity: int = 0
    strategy: str = "twap"
    side: Side = Side.BUY
    participation_rate: float = 0.08
    limit_price_ticks: int | None = None
    target_symbol: str = "NOVA"

    def configure(self, spec: WorldSpec) -> None:
        self.target_quantity = spec.experiment.parent_order.quantity
        self.strategy = spec.experiment.strategy
        self.side = Side(spec.experiment.parent_order.side)
        self.participation_rate = spec.experiment.participation_rate
        self.limit_price_ticks = spec.experiment.parent_order.limit_price_ticks
        self.target_symbol = spec.experiment.target_asset

    def decide(self, context: AgentContext, rng: random.Random, inventory: int) -> list[Order]:
        remaining = self.target_quantity - self.executed_quantity
        if remaining <= 0 or context.symbol != self.target_symbol:
            return []
        if self.strategy == "pov":
            quantity = max(10, int(context.observed_volume * self.participation_rate))
        else:
            quantity = max(10, self.target_quantity // 80)
        quantity = min(remaining, quantity)
        if self.limit_price_ticks is not None:
            return [Order(self.next_id(), self.agent_id, context.symbol, self.side, OrderType.LIMIT, quantity,
                          context.step, self.limit_price_ticks)]
        order = self.market(context, self.side, quantity)
        return [order] if order else []


AGENT_CLASS = {
    "market_maker": MarketMaker, "fundamental": FundamentalTrader, "momentum": MomentumTrader,
    "mean_reversion": MeanReversionTrader, "noise": NoiseTrader, "forced_liquidator": ForcedLiquidator,
    "execution": ExecutionAgent,
}


def build_agents(populations: list[AgentPopulation], spec: WorldSpec) -> list[BaseAgent]:
    agents: list[BaseAgent] = []
    for population in populations:
        cls = AGENT_CLASS[population.type]
        for index in range(population.count):
            agent = cls(f"{population.type}-{index + 1:02d}", population.type, population.latency_ms,
                        population.capital_cents, population.risk_limit_shares, dict(population.parameters))
            if isinstance(agent, ExecutionAgent):
                agent.configure(spec)
            agents.append(agent)
    return agents
