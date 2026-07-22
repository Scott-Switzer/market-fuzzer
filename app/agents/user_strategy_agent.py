from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.agents.behaviors import AgentContext, BaseAgent
from app.break_test.strategies import compute_positions
from app.exchange import Order, OrderType, Side


@dataclass
class UserStrategyAgent(BaseAgent):
    target_quantity: int = 0
    strategy_type: str = "sma_crossover"
    params: dict[str, int] | None = None
    side: Side = Side.BUY
    target_symbol: str = "ASSET"
    lot_size: int = 1

    def decide(
        self, context: AgentContext, rng: random.Random, inventory: int
    ) -> list[Order]:
        recent = list(context.recent_prices)
        if context.mid_ticks > 0:
            if not recent:
                recent = [context.mid_ticks]
            elif recent[-1] != context.mid_ticks:
                recent = [*recent, context.mid_ticks]
        if len(recent) < 5:
            return []
        try:
            positions = compute_positions(
                self.strategy_type,
                np.array(recent, dtype=float),
                **(self.params or {}),
            )
        except ValueError:
            return []
        desired = float(positions[-1]) if len(positions) else 0.0
        current = 1.0 if inventory > 0 else 0.0 if inventory < 0 else 0.0
        if desired <= current or context.symbol != self.target_symbol:
            return []
        quantity = max(
            self.lot_size,
            min(
                self.target_quantity,
                max(self.lot_size, int(self.target_quantity * 0.1)),
            ),
        )
        quantity = (quantity // self.lot_size) * self.lot_size
        order = self.market(context, self.side, quantity)
        return [order] if order else []
