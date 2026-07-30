"""Built-in strategy templates (reset brief Phase 2 item 26).

Templates are FACTORIES for the same canonical ``StrategySpec`` -- they do NOT
invoke separate executor code. A template and its plain-English equivalent must
produce semantically equivalent specs (identical canonical hash when all semantic
fields are equal).
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.strategy_spec import (
    Frequency,
    MomentumSignal,
    PortfolioConstruction,
    RiskConstraints,
    SimpleMovingAverageSignal,
    StrategySpec,
    StrategyType,
    Weighting,
)


def static_6040(voo: str = "SPY", agg: str = "AGG", *, name: str = "60/40") -> StrategySpec:
    return StrategySpec(
        name=name,
        original_thesis=f"Allocate 60% to {voo} and 40% to {agg} and rebalance monthly.",
        strategy_type=StrategyType.STATIC_ALLOCATION,
        universe=[voo, agg],
        frequency=Frequency.MONTHLY,
        rebalance_policy=Frequency.MONTHLY,
        long_short_policy="long_only",
        portfolio_construction=PortfolioConstruction(
            weighting=Weighting.FIXED,
            target_weights={voo: Decimal("0.600000"), agg: Decimal("0.400000")},
        ),
    )


def sma_crossover(
    ticker: str = "SPY", fast: int = 20, slow: int = 50, *, name: str = "SMA crossover"
) -> StrategySpec:
    return StrategySpec(
        name=name,
        original_thesis=(
            f"Buy {ticker} when its {fast}-day average is above its {slow}-day average, otherwise hold cash."
        ),
        strategy_type=StrategyType.TIME_SERIES_SIGNAL,
        universe=[ticker],
        frequency=Frequency.DAILY,
        rebalance_policy=Frequency.DAILY,
        long_short_policy="long_only",
        signal_definitions=[SimpleMovingAverageSignal(id="sma", fast_window=fast, slow_window=slow)],
    )


def long_only_momentum(
    universe: list[str], top_n: int = 5, *, name: str = "Momentum rotation"
) -> StrategySpec:
    return StrategySpec(
        name=name,
        original_thesis=f"Buy the top {top_n} stocks by 12-1 momentum each month.",
        strategy_type=StrategyType.LONG_ONLY_RANKING,
        universe=universe,
        frequency=Frequency.MONTHLY,
        rebalance_policy=Frequency.MONTHLY,
        long_short_policy="long_only",
        signal_definitions=[MomentumSignal(id="mom")],
        portfolio_construction=PortfolioConstruction(long_count=top_n),
        risk_constraints=RiskConstraints(
            gross_exposure_limit=Decimal("1.0"), max_position_weight=Decimal("0.5")
        ),
    )


TEMPLATES = {
    "static_6040": static_6040,
    "sma_crossover": sma_crossover,
    "long_only_momentum": long_only_momentum,
}

__all__ = ["static_6040", "sma_crossover", "long_only_momentum", "TEMPLATES"]
