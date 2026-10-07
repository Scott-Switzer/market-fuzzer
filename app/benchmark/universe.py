"""Synthetic securities universe for the exchange benchmark (M10.5).

The universe is built from the M10 daily market engine: a cross-sectional factor
structure whose innovations are GJR-GARCH-t. The engine produces daily OHLC paths
for every synthetic company; the intraday limit-order-book session then emerges
from the background agents around those fundamental anchors.

Holdout profiles implement *mechanism holdout*: the public family and the hidden
family differ in volatility, liquidity depth, fee schedule, and background-agent
mix while preserving the same public interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.market.engine import EntitySpec, GjrGarchT, MarketWorld, simulate_daily_market
from app.world.rng import SemanticRNG

__all__ = [
    "BenchmarkUniverse",
    "HIDDEN_PROFILE",
    "HoldoutProfile",
    "LiquidityRegime",
    "PUBLIC_PROFILE",
    "Security",
    "build_universe",
]

HoldoutClass = Literal["public", "hidden"]
LiquidityRegime = Literal["deep", "normal", "thin"]

SECTORS: tuple[str, ...] = (
    "SYNTH_TECHNOLOGY",
    "SYNTH_ENERGY",
    "SYNTH_FINANCIALS",
    "SYNTH_HEALTHCARE",
    "SYNTH_INDUSTRIALS",
    "SYNTH_CONSUMER",
    "SYNTH_MATERIALS",
    "SYNTH_UTILITIES",
)

_PUBLIC_LIQUIDITY: tuple[LiquidityRegime, ...] = ("normal", "deep", "thin", "normal")
_HIDDEN_LIQUIDITY: tuple[LiquidityRegime, ...] = ("thin", "thin", "deep", "normal")


@dataclass(frozen=True, slots=True)
class HoldoutProfile:
    """A generated-market family. Hidden profiles exercise mechanism holdout."""

    label: str
    holdout: HoldoutClass
    volatility_scale: float
    depth_scale: float
    maker_fee_bps: int
    taker_fee_bps: int
    momentum_crowding: float
    market_makers: int
    fundamental_traders: int
    momentum_traders: int
    noise_traders: int

    def __post_init__(self) -> None:
        if self.holdout not in {"public", "hidden"}:
            raise ValueError("holdout must be 'public' or 'hidden'")
        if self.volatility_scale <= 0 or self.depth_scale <= 0:
            raise ValueError("volatility and depth scales must be positive")
        if min(self.maker_fee_bps, self.taker_fee_bps, self.momentum_crowding) < 0:
            raise ValueError("fees and crowding must be non-negative")
        if min(self.market_makers, self.fundamental_traders, self.momentum_traders, self.noise_traders) < 0:
            raise ValueError("agent populations must be non-negative")


PUBLIC_PROFILE = HoldoutProfile(
    label="public-baseline",
    holdout="public",
    volatility_scale=1.0,
    depth_scale=1.0,
    maker_fee_bps=0,
    taker_fee_bps=0,
    momentum_crowding=1.0,
    market_makers=3,
    fundamental_traders=2,
    momentum_traders=1,
    noise_traders=3,
)

HIDDEN_PROFILE = HoldoutProfile(
    label="hidden-mechanism-holdout",
    holdout="hidden",
    volatility_scale=1.7,
    depth_scale=0.55,
    maker_fee_bps=0,
    taker_fee_bps=1,
    momentum_crowding=2.2,
    market_makers=2,
    fundamental_traders=1,
    momentum_traders=3,
    noise_traders=3,
)


@dataclass(frozen=True, slots=True)
class Security:
    """One synthetic security and its generated daily fundamentals."""

    symbol: str
    sector: str
    initial_price_ticks: int
    liquidity: LiquidityRegime
    beta: float
    sector_beta: float
    drift: float
    shares_outstanding: int
    daily_open_ticks: tuple[int, ...]
    daily_close_ticks: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class BenchmarkUniverse:
    """A generated set of synthetic securities over a fixed session calendar."""

    universe_id: str
    world_id: str
    seed: int
    holdout: HoldoutClass
    profile_label: str
    sessions: tuple[date, ...]
    securities: tuple[Security, ...]
    market_logical_sha256: str

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(security.symbol for security in self.securities)

    def security(self, symbol: str) -> Security:
        for security in self.securities:
            if security.symbol == symbol:
                return security
        raise KeyError(symbol)


def _scaled_garch(
    scale: float, *, omega: float, alpha: float, gamma: float, beta: float, nu: float
) -> GjrGarchT:
    """A GJR-GARCH-t with its variance scaled by ``scale`` (persistence unchanged)."""

    return GjrGarchT(omega * scale * scale, alpha, gamma, beta, nu)


def _build_market_world(
    *, world_id: str, seed: int, sessions: tuple[date, ...], securities: tuple[Security, ...], scale: float
) -> MarketWorld:
    sectors = sorted({security.sector for security in securities})
    return MarketWorld(
        world_id=world_id,
        seed=seed,
        sessions=sessions,
        market=_scaled_garch(scale, omega=4.0e-6, alpha=0.03, gamma=0.09, beta=0.88, nu=6.0),
        sectors={
            sector: _scaled_garch(scale, omega=2.5e-6, alpha=0.04, gamma=0.08, beta=0.86, nu=7.0)
            for sector in sectors
        },
        entities=tuple(
            EntitySpec(
                symbol=security.symbol,
                sector=security.sector,
                beta=security.beta,
                sector_beta=security.sector_beta,
                drift=security.drift,
                garch=_scaled_garch(scale, omega=6.0e-6, alpha=0.05, gamma=0.10, beta=0.83, nu=5.0),
            )
            for security in securities
        ),
    )


def _scaled_ticks(price_ticks: float, ratio: float) -> int:
    return max(1, round(price_ticks * ratio))


def build_universe(
    *,
    universe_id: str,
    world_id: str,
    seed: int,
    profile: HoldoutProfile,
    security_count: int,
    sessions: tuple[date, ...],
) -> BenchmarkUniverse:
    """Build a deterministic synthetic universe and its fundamental price paths."""

    if security_count < 1:
        raise ValueError("a universe needs at least one security")
    if not sessions:
        raise ValueError("a universe needs at least one session")
    rng = SemanticRNG(world_id, seed)
    sector_count = min(len(SECTORS), max(4, security_count // 4))
    sector_names = SECTORS[:sector_count]
    liquidity_cycle = _HIDDEN_LIQUIDITY if profile.holdout == "hidden" else _PUBLIC_LIQUIDITY

    provisional_list: list[Security] = []
    for index in range(1, security_count + 1):
        symbol = f"SYN{index:03d}"
        ordinal = index - 1
        stream = rng.stream(f"COMPANY:{symbol}", "benchmark.universe")
        provisional_list.append(
            Security(
                symbol=symbol,
                sector=sector_names[ordinal % sector_count],
                initial_price_ticks=1_000 + int(stream.uniform("initial_price", ordinal) * 49_000),
                liquidity=liquidity_cycle[ordinal % len(liquidity_cycle)],
                beta=round(stream.uniform_range("beta", 0.40, 1.60, ordinal), 4),
                sector_beta=round(stream.uniform_range("sector_beta", 0.10, 0.80, ordinal), 4),
                drift=round(stream.uniform_range("drift", -0.0004, 0.0008, ordinal), 6),
                shares_outstanding=int(1_000_000 + stream.uniform("shares", ordinal) * 99_000_000),
                daily_open_ticks=(),
                daily_close_ticks=(),
            )
        )
    provisional = tuple(provisional_list)

    market_world = _build_market_world(
        world_id=world_id,
        seed=seed,
        sessions=sessions,
        securities=provisional,
        scale=profile.volatility_scale,
    )
    market = simulate_daily_market(market_world)

    securities: list[Security] = []
    for security in provisional:
        series = market.series[security.symbol]
        base = float(security.initial_price_ticks)
        opens = tuple(_scaled_ticks(base, bar.open / 100.0) for bar in series.bars)
        closes = tuple(_scaled_ticks(base, bar.close / 100.0) for bar in series.bars)
        securities.append(
            Security(
                symbol=security.symbol,
                sector=security.sector,
                initial_price_ticks=security.initial_price_ticks,
                liquidity=security.liquidity,
                beta=security.beta,
                sector_beta=security.sector_beta,
                drift=security.drift,
                shares_outstanding=security.shares_outstanding,
                daily_open_ticks=opens,
                daily_close_ticks=closes,
            )
        )

    return BenchmarkUniverse(
        universe_id=universe_id,
        world_id=world_id,
        seed=seed,
        holdout=profile.holdout,
        profile_label=profile.label,
        sessions=sessions,
        securities=tuple(securities),
        market_logical_sha256=market.logical_sha256,
    )
