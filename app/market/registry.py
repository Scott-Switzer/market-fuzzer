"""Causal-mechanism declarations for the M10 daily market.

The market mechanisms are registered as day-frequency nodes in the M6 causal
registry so the twin-world invariance guarantee applies: ``event_jump`` is
declared intervenable, and clamping one company's jumps leaves every
non-descendant series unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.causal.registry import MechanismRegistry, MechanismSpec

__all__ = ["MARKET_MECHANISMS", "market_registry"]

MARKET_MECHANISMS: tuple[MechanismSpec, ...] = (
    MechanismSpec(
        id="market.factor",
        version=1,
        scope="world",
        inputs=(),
        outputs=("market_factor_return",),
        rng_addresses=("market.factor@t",),
        frequency="day",
        doc="World market factor log-return from a GJR-GARCH-t process.",
    ),
    MechanismSpec(
        id="sector.factor",
        version=1,
        scope="sector",
        inputs=("market_factor_return",),
        outputs=("sector_factor_return",),
        rng_addresses=("sector.factor@t",),
        frequency="day",
        doc="Sector factor log-return driven by the world factor plus its own GJR-GARCH-t process.",
    ),
    MechanismSpec(
        id="market.idiosyncratic",
        version=1,
        scope="company",
        inputs=("idiosyncratic_return@t-1",),
        outputs=("idiosyncratic_return",),
        rng_addresses=("market.idiosyncratic@t",),
        frequency="day",
        doc="Company idiosyncratic log-return from a GJR-GARCH-t process with a lagged self term.",
    ),
    MechanismSpec(
        id="market.event_jump",
        version=1,
        scope="company",
        inputs=(),
        outputs=("event_jump",),
        frequency="day",
        doc="Discrete event jump in log-return; intervenable for counterfactual twins.",
    ),
    MechanismSpec(
        id="market.asset_return",
        version=1,
        scope="company",
        inputs=(
            "market_factor_return",
            "sector_factor_return",
            "idiosyncratic_return",
            "event_jump",
            "log_return@t-1",
        ),
        outputs=("log_return",),
        frequency="day",
        doc="Cross-sectional factor combination: drift + betas*factors + idio + jump.",
    ),
    MechanismSpec(
        id="market.close",
        version=1,
        scope="company",
        inputs=("log_return", "close@t-1"),
        outputs=("close",),
        frequency="day",
        doc="Close price as the cumulative product of log returns.",
    ),
    MechanismSpec(
        id="market.ohlc",
        version=1,
        scope="company",
        inputs=("close", "close@t-1"),
        outputs=("open", "high", "low"),
        rng_addresses=("market.ohlc@t",),
        frequency="day",
        doc="Brownian-bridge OHLC around the close; bounds enforced by MKT-OHLC-BOUNDS.",
    ),
)


def market_registry() -> MechanismRegistry:
    """A validated registry declaring every daily market mechanism."""

    registry = MechanismRegistry()
    for spec in MARKET_MECHANISMS:
        registry.register(spec)
    registry.allow_intervention("event_jump")
    return registry.validate()


def sector_of_company(symbols: Mapping[str, str]) -> dict[str, str]:
    """Identity helper: ``{symbol: sector}`` (kept explicit for readability at call sites)."""

    return dict(symbols)
