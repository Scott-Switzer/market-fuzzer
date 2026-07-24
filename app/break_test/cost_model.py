"""Compatibility shim: Almgren-Chriss helpers live in ``costs`` / ``metrics``.

Kept so existing imports of ``app.break_test.cost_model`` continue to resolve.
"""

from __future__ import annotations

from app.break_test.costs import (
    almgren_chriss_impact_bps,
    borrow_fee_bps_for_short,
    lookup_htb_bps_annual,
    toxicity_bps,
)
from app.break_test.metrics import (
    CostModelResult,
    _borrow_fee_bps,
    _clamp,
    _estimate_daily_vol,
    _impact_bps,
    _spread_bps,
    _tiered_fee_bps,
    compute_turnover_cost,
    cost_for_trade,
)

__all__ = [
    "CostModelResult",
    "almgren_chriss_impact_bps",
    "borrow_fee_bps_for_short",
    "compute_turnover_cost",
    "cost_for_trade",
    "lookup_htb_bps_annual",
    "toxicity_bps",
    "_borrow_fee_bps",
    "_clamp",
    "_estimate_daily_vol",
    "_impact_bps",
    "_spread_bps",
    "_tiered_fee_bps",
]
