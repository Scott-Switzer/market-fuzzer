"""Signal computations shared by executors (reset brief Phase 2 items 18, 20).

All signals are point-in-time safe: value at bar t uses only data at or before t.
No automatic shortening of lookback windows -- insufficient history yields NaN
(ineligible), never a silently altered strategy.
"""

from __future__ import annotations

from app.strategies.signals.momentum import momentum_12_1, total_return
from app.strategies.signals.moving_average import average_rank, simple_moving_average
from app.strategies.signals.volatility import realized_volatility

__all__ = [
    "momentum_12_1",
    "total_return",
    "realized_volatility",
    "simple_moving_average",
    "average_rank",
]
