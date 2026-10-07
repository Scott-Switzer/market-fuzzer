"""Deterministic background-agent archetypes for the synthetic exchange benchmark.

Each archetype emits abstract :class:`BackgroundIntent` values. The session owns
command-ID, venue-sequence, and time allocation, so agents never touch the
exchange directly and their order flow stays totally ordered in the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.benchmark.universe import HoldoutProfile
from app.exchange.v2 import OrderTypeV2, SideV2, TimeInForceV2
from app.world.rng import SemanticStream

__all__ = [
    "BookView",
    "BackgroundAgent",
    "CancelIntent",
    "FundamentalTraderAgent",
    "MarketMakerAgent",
    "MomentumTraderAgent",
    "NoiseTraderAgent",
    "SubmitIntent",
    "build_background_agents",
]


@dataclass(frozen=True, slots=True)
class BookView:
    """A read-only snapshot of one instrument the background agents reason over."""

    instrument_id: str
    step: int
    fundamental_ticks: int
    mid_ticks: int | None
    best_bid_ticks: int | None
    best_ask_ticks: int | None
    last_price_ticks: int | None
    recent_prices: tuple[int, ...]
    open_order_ids: tuple[str, ...]
    depth_scale: float


@dataclass(frozen=True, slots=True)
class SubmitIntent:
    side: SideV2
    quantity: int
    price_ticks: int | None
    order_type: OrderTypeV2 = OrderTypeV2.LIMIT
    time_in_force: TimeInForceV2 = TimeInForceV2.DAY

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("submit intent quantity must be positive")


@dataclass(frozen=True, slots=True)
class CancelIntent:
    order_id: str

    def __post_init__(self) -> None:
        if not self.order_id:
            raise ValueError("cancel intent requires an order_id")


BackgroundIntent = SubmitIntent | CancelIntent


class BackgroundAgent(Protocol):
    """A deterministic liquidity/flow provider bound to one exchange account."""

    agent_id: str
    account_id: str

    def act(self, view: BookView, stream: SemanticStream) -> list[BackgroundIntent]: ...


def _crossing_price(view: BookView, side: SideV2, aggression_ticks: int) -> int:
    """A limit price that crosses the current touch (used with IOC takers)."""

    if side == SideV2.BUY:
        reference = view.best_ask_ticks or view.mid_ticks or view.fundamental_ticks
        return max(1, reference + aggression_ticks)
    reference = view.best_bid_ticks or view.mid_ticks or view.fundamental_ticks
    return max(1, reference - aggression_ticks)


@dataclass
class MarketMakerAgent:
    """Posts a symmetric multi-level quote ladder and refreshes it every step."""

    agent_id: str
    account_id: str
    spread_ticks: int = 3
    levels: int = 3
    size: int = 300

    def act(self, view: BookView, stream: SemanticStream) -> list[BackgroundIntent]:
        intents: list[BackgroundIntent] = [CancelIntent(order_id) for order_id in view.open_order_ids]
        reference = view.mid_ticks or view.fundamental_ticks
        reservation = round(0.7 * reference + 0.3 * view.fundamental_ticks)
        jitter = round(stream.normal("mm.spread_jitter", view.step, 0))
        spread = max(1, self.spread_ticks + jitter)
        size = max(1, int(self.size * view.depth_scale))
        for level in range(1, self.levels + 1):
            intents.append(SubmitIntent(SideV2.BUY, size, max(1, reservation - spread * level)))
            intents.append(SubmitIntent(SideV2.SELL, size, max(1, reservation + spread * level)))
        return intents


@dataclass
class FundamentalTraderAgent:
    """Takes liquidity when the book drifts away from the fundamental anchor."""

    agent_id: str
    account_id: str
    threshold: float = 0.0015
    sensitivity: float = 4000.0
    max_size: int = 250
    aggression_ticks: int = 2

    def act(self, view: BookView, stream: SemanticStream) -> list[BackgroundIntent]:
        mid = view.mid_ticks or view.fundamental_ticks
        if mid <= 0:
            return []
        gap = view.fundamental_ticks / mid - 1.0
        if abs(gap) < self.threshold:
            return []
        side = SideV2.BUY if gap > 0 else SideV2.SELL
        quantity = min(self.max_size, max(10, int(abs(gap) * self.sensitivity * view.depth_scale)))
        price = _crossing_price(view, side, self.aggression_ticks)
        return [SubmitIntent(side, quantity, price, time_in_force=TimeInForceV2.IOC)]


@dataclass
class MomentumTraderAgent:
    """Chases recent price changes with marketable (IOC) limit orders."""

    agent_id: str
    account_id: str
    lookback: int = 5
    size: int = 60
    crowding: float = 1.0
    aggression_ticks: int = 2

    def act(self, view: BookView, stream: SemanticStream) -> list[BackgroundIntent]:
        if len(view.recent_prices) <= self.lookback:
            return []
        change = view.recent_prices[-1] - view.recent_prices[-1 - self.lookback]
        if change == 0:
            return []
        side = SideV2.BUY if change > 0 else SideV2.SELL
        quantity = max(10, int(self.size * self.crowding))
        price = _crossing_price(view, side, self.aggression_ticks)
        return [SubmitIntent(side, quantity, price, time_in_force=TimeInForceV2.IOC)]


@dataclass
class NoiseTraderAgent:
    """Adds resting liquidity away from the touch at a random intensity."""

    agent_id: str
    account_id: str
    activity: float = 0.5
    min_size: int = 10
    max_size: int = 90

    def act(self, view: BookView, stream: SemanticStream) -> list[BackgroundIntent]:
        if stream.uniform("noise.active", view.step, 0) > self.activity:
            return []
        mid = view.mid_ticks or view.fundamental_ticks
        side = SideV2.BUY if stream.uniform("noise.side", view.step, 0) > 0.5 else SideV2.SELL
        offset = stream.randint("noise.offset", 1, 4, view.step)
        price = max(1, mid - offset if side == SideV2.BUY else mid + offset)
        quantity = max(1, stream.randint("noise.size", self.min_size, self.max_size, view.step))
        return [SubmitIntent(side, quantity, price)]


def build_background_agents(profile: HoldoutProfile) -> tuple[BackgroundAgent, ...]:
    """Instantiate the profile's agent population in a stable order."""

    agents: list[BackgroundAgent] = []
    for index in range(profile.market_makers):
        agents.append(MarketMakerAgent(f"mm-{index + 1:02d}", f"mm-{index + 1:02d}"))
    for index in range(profile.fundamental_traders):
        agents.append(FundamentalTraderAgent(f"fund-{index + 1:02d}", f"fund-{index + 1:02d}"))
    for index in range(profile.momentum_traders):
        agents.append(
            MomentumTraderAgent(
                f"mom-{index + 1:02d}", f"mom-{index + 1:02d}", crowding=profile.momentum_crowding
            )
        )
    for index in range(profile.noise_traders):
        agents.append(NoiseTraderAgent(f"noise-{index + 1:02d}", f"noise-{index + 1:02d}"))
    return tuple(agents)
