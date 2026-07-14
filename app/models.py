from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal


ScenarioName = Literal["normal", "liquidity_withdrawal", "earnings_shock", "crowded_unwind"]


@dataclass(frozen=True)
class WorldSpec:
    """All exogenous parameters required to replay a synthetic market world."""

    name: str = "Fragile small-cap market"
    seed: int = 42
    scenario: ScenarioName = "normal"
    symbols: tuple[str, ...] = ("NOVA", "ORBT", "VYNE")
    steps: int = 90
    initial_price: float = 100.0
    base_depth: int = 450
    base_spread_bps: float = 8.0
    volatility: float = 0.004
    strategy_participation: float = 0.08
    parent_order_shares: int = 1_800
    event_step: int = 45
    metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        value = asdict(self)
        value["symbols"] = list(self.symbols)
        return value

