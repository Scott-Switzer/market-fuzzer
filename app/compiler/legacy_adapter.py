"""One-way legacy flagship adapter (reset brief item 23).

Converts the legacy ``CrossSectionalSpec`` dataclass into a canonical
``StrategySpec`` v1.1 so the new executor can be run on the same inputs for
parity testing. The new executor does NOT depend on ``CrossSectionalSpec`` --
this adapter is the only bridge, and it points one way (legacy -> canonical).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.domain.strategy_spec import (
    CostModel,
    Frequency,
    MomentumSignal,
    PortfolioConstruction,
    RealizedVolatilitySignal,
    RiskConstraints,
    StrategySpec,
    StrategyType,
)


def cross_sectional_to_spec(legacy: Any, *, name: str = "Flagship cross-sectional") -> StrategySpec:
    """Adapt a legacy CrossSectionalSpec to a canonical StrategySpec v1.1."""
    universe = [a for a in legacy.universe]
    return StrategySpec(
        name=name,
        original_thesis=(
            "Cross-sectional composite of 12-1 momentum and low realized volatility; "
            "monthly long top / short bottom quantiles, dollar-neutral, capped."
        ),
        strategy_type=StrategyType.CROSS_SECTIONAL_FACTOR,
        universe=universe,
        benchmark=legacy.benchmark,
        benchmark_tradable=False,
        frequency=Frequency.MONTHLY,
        rebalance_policy=Frequency.MONTHLY,
        long_short_policy="long_short",
        signal_definitions=[
            MomentumSignal(id="mom", lookback=legacy.momentum_lookback, skip=legacy.momentum_short),
            RealizedVolatilitySignal(id="vol", lookback=legacy.volatility_window),
        ],
        signal_combination={
            "momentum": Decimal(f"{legacy.momentum_weight}"),
            "low_volatility": Decimal(f"{legacy.low_volatility_weight}"),
        },
        portfolio_construction=PortfolioConstruction(
            long_short=True,
            long_quantile=Decimal(f"{legacy.long_quantile}"),
            short_quantile=Decimal(f"{legacy.short_quantile}"),
        ),
        risk_constraints=RiskConstraints(
            gross_exposure_limit=Decimal(f"{legacy.gross_exposure}"),
            net_exposure_target=Decimal(f"{legacy.net_exposure}"),
            max_position_weight=Decimal(f"{legacy.max_position_weight}"),
        ),
        cost_model=CostModel(
            commission_bps=Decimal(f"{legacy.commission_bps}"),
            spread_bps=Decimal(f"{legacy.spread_bps}"),
            slippage_bps=Decimal(f"{legacy.slippage_bps}"),
            borrow_bps_annual=Decimal(f"{legacy.borrow_bps}"),
            locate_bps=Decimal(f"{legacy.locate_bps}"),
        ),
    )


__all__ = ["cross_sectional_to_spec"]
