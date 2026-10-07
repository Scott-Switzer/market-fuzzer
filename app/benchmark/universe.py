"""Synthetic securities universe for the exchange benchmark (M10.6).

The universe is built from the M10 daily market engine: a cross-sectional factor
structure whose innovations come from a selectable *process family*. The engine
produces daily OHLC paths for every synthetic company; the intraday
limit-order-book session then emerges from the background agents around those
fundamental anchors.

M10.6 separates the two axes that M10.5 conflated:

* the **process family** -- how innovations are generated (GJR-GARCH-t,
  stochastic volatility, or regime-jump). See :mod:`app.market.process`.
* the **ecology** -- the tradable environment around the same price process:
  volatility level, displayed depth, fee schedule, and background-agent mix
  (:class:`EcologyProfile`).

Evaluation partitions combine the two:

* ``familiar``     -- baseline ecology, the familiar process family.
* ``distribution`` -- shifted ecology, the familiar process family (the M10.5
  parameter/ecology holdout).
* ``mechanism``    -- shifted ecology, a process family the agent never saw.

Every family is normalized to the same unconditional per-session variance per
node, so a score change on the mechanism partition reflects the *dynamics* of the
generator, not a volatility-level shift.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Literal

from app.market.engine import EntitySpec, GjrGarchT, MarketWorld, ProcessFamily, simulate_daily_market
from app.market.process import (
    FAMILIAR_FAMILY,
    MarkovRegimeJumpFactorT,
    ProcessFamilyKind,
    StochasticVolFactorT,
)
from app.world.rng import SemanticRNG

__all__ = [
    "DISTRIBUTION_ECOLOGY",
    "EvaluationPartition",
    "EcologyProfile",
    "FAMILIAR_ECOLOGY",
    "FAMILIAR_FAMILY",
    "LiquidityRegime",
    "PUBLIC_PROFILE",
    "HIDDEN_PROFILE",
    "Security",
    "BenchmarkUniverse",
    "build_universe",
]

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


class EvaluationPartition(StrEnum):
    """The three M10.6 evaluation partitions."""

    FAMILIAR = "familiar"
    DISTRIBUTION = "distribution"
    MECHANISM = "mechanism"


@dataclass(frozen=True, slots=True)
class EcologyProfile:
    """The tradable environment around a price process.

    An ecology carries no information about the generating process family; it
    only describes volatility level, displayed depth, fees, and the background
    participant mix.
    """

    label: str
    volatility_scale: float
    depth_scale: float
    maker_fee_bps: int
    taker_fee_bps: int
    momentum_crowding: float
    market_makers: int
    fundamental_traders: int
    momentum_traders: int
    noise_traders: int
    liquidity_cycle: tuple[LiquidityRegime, ...]

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("an ecology profile needs a label")
        if self.volatility_scale <= 0 or self.depth_scale <= 0:
            raise ValueError("volatility and depth scales must be positive")
        if min(self.maker_fee_bps, self.taker_fee_bps, self.momentum_crowding) < 0:
            raise ValueError("fees and crowding must be non-negative")
        if min(self.market_makers, self.fundamental_traders, self.momentum_traders, self.noise_traders) < 0:
            raise ValueError("agent populations must be non-negative")
        if not self.liquidity_cycle:
            raise ValueError("a liquidity cycle needs at least one regime")


FAMILIAR_ECOLOGY = EcologyProfile(
    label="familiar-baseline",
    volatility_scale=1.0,
    depth_scale=1.0,
    maker_fee_bps=0,
    taker_fee_bps=0,
    momentum_crowding=1.0,
    market_makers=3,
    fundamental_traders=2,
    momentum_traders=1,
    noise_traders=3,
    liquidity_cycle=("normal", "deep", "thin", "normal"),
)

DISTRIBUTION_ECOLOGY = EcologyProfile(
    label="distribution-shift",
    volatility_scale=1.7,
    depth_scale=0.55,
    maker_fee_bps=0,
    taker_fee_bps=1,
    momentum_crowding=2.2,
    market_makers=2,
    fundamental_traders=1,
    momentum_traders=3,
    noise_traders=3,
    liquidity_cycle=("thin", "thin", "deep", "normal"),
)

# M10.5 names, retained so older callers keep working. Both are now pure
# ecologies -- they no longer carry a partition or a process family.
PUBLIC_PROFILE = FAMILIAR_ECOLOGY
HIDDEN_PROFILE = DISTRIBUTION_ECOLOGY


# --- process-family construction ------------------------------------------------
#
# Base GJR-GARCH-t parameters per node role (M10.5 values). The two held-out
# families are normalized to the same unconditional per-session variance, so the
# family axis changes the *dynamics* of the path, not its scale.

_GJR_PARAMS: dict[str, dict[str, float]] = {
    "market": {"omega": 4.0e-6, "alpha": 0.03, "gamma": 0.09, "beta": 0.88, "nu": 6.0},
    "sector": {"omega": 2.5e-6, "alpha": 0.04, "gamma": 0.08, "beta": 0.86, "nu": 7.0},
    "entity": {"omega": 6.0e-6, "alpha": 0.05, "gamma": 0.10, "beta": 0.83, "nu": 5.0},
}

# (phi, sigma_eta, nu) for the stochastic-volatility family, per node role.
_SV_SHAPE: dict[str, tuple[float, float, float]] = {
    "market": (0.96, 0.14, 5.0),
    "sector": (0.95, 0.15, 6.0),
    "entity": (0.97, 0.13, 4.5),
}

# Shared regime-jump template; the per-role instance is rescaled to match the
# GJR baseline's unconditional variance.
_MRJ_TEMPLATE: dict[str, Any] = {
    "transition": ((0.95, 0.05), (0.20, 0.80)),
    "means": (0.0006, -0.0015),
    "sigmas": (0.006, 0.018),
    "jump_scale": (0.4, 2.0),
    "jump_prob": 0.02,
    "jump_mean": -0.0002,
    "jump_sigma": 0.02,
    "nu": 5.0,
}


def _gjr_base_variance(role: str) -> float:
    params = _GJR_PARAMS[role]
    persistence = params["alpha"] + 0.5 * params["gamma"] + params["beta"]
    return params["omega"] / (1.0 - persistence)


def _scaled_garch(scale: float, *, role: str) -> GjrGarchT:
    """A GJR-GARCH-t with its variance scaled by ``scale`` (persistence unchanged)."""

    params = _GJR_PARAMS[role]
    return GjrGarchT(
        omega=params["omega"] * scale * scale,
        alpha=params["alpha"],
        gamma=params["gamma"],
        beta=params["beta"],
        nu=params["nu"],
    )


def _stochastic_vol(*, role: str, variance_scale: float) -> StochasticVolFactorT:
    phi, sigma_eta, nu = _SV_SHAPE[role]
    log_variance = sigma_eta**2 / (1.0 - phi**2)
    # E[eps^2] = exp(mu + log_variance / 2), so match the GJR baseline exactly.
    mu = math.log(_gjr_base_variance(role)) - 0.5 * log_variance
    return StochasticVolFactorT(mu=mu, phi=phi, sigma_eta=sigma_eta, nu=nu, variance_scale=variance_scale)


def _markov_regime_jump(*, role: str, variance_scale: float) -> MarkovRegimeJumpFactorT:
    """A regime-jump family rescaled to the GJR baseline's unconditional variance.

    Every innovation component (regime mean, regime volatility, and jump size) is
    scaled by a common factor, so the whole path scales by that factor and the
    matching is exact.
    """

    transition = _MRJ_TEMPLATE["transition"]
    jump_scale = _MRJ_TEMPLATE["jump_scale"]
    base = MarkovRegimeJumpFactorT(
        means=_MRJ_TEMPLATE["means"],
        sigmas=_MRJ_TEMPLATE["sigmas"],
        jump_scale=jump_scale,
        transition=transition,
        jump_prob=_MRJ_TEMPLATE["jump_prob"],
        jump_mean=_MRJ_TEMPLATE["jump_mean"],
        jump_sigma=_MRJ_TEMPLATE["jump_sigma"],
        nu=_MRJ_TEMPLATE["nu"],
    )
    factor = math.sqrt(_gjr_base_variance(role) / base.unconditional_variance())
    return MarkovRegimeJumpFactorT(
        means=tuple(value * factor for value in _MRJ_TEMPLATE["means"]),
        sigmas=tuple(value * factor for value in _MRJ_TEMPLATE["sigmas"]),
        jump_scale=jump_scale,
        transition=transition,
        jump_prob=_MRJ_TEMPLATE["jump_prob"],
        jump_mean=_MRJ_TEMPLATE["jump_mean"] * factor,
        jump_sigma=_MRJ_TEMPLATE["jump_sigma"] * factor,
        nu=_MRJ_TEMPLATE["nu"],
        variance_scale=variance_scale,
    )


def _family_for(family: ProcessFamilyKind, role: str, scale: float) -> ProcessFamily:
    """Instantiate one node of ``family`` at ecology volatility ``scale``."""

    if family is ProcessFamilyKind.GJR_FACTOR_T_V1:
        return _scaled_garch(scale, role=role)
    if family is ProcessFamilyKind.STOCHASTIC_VOL_FACTOR_T_V1:
        return _stochastic_vol(role=role, variance_scale=scale)
    return _markov_regime_jump(role=role, variance_scale=scale)


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
    partition: str
    process_family: str
    ecology_label: str
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


def _build_market_world(
    *,
    world_id: str,
    seed: int,
    sessions: tuple[date, ...],
    securities: tuple[Security, ...],
    family: ProcessFamilyKind,
    scale: float,
) -> MarketWorld:
    sectors = sorted({security.sector for security in securities})
    return MarketWorld(
        world_id=world_id,
        seed=seed,
        sessions=sessions,
        market=_family_for(family, "market", scale),
        sectors={sector: _family_for(family, "sector", scale) for sector in sectors},
        entities=tuple(
            EntitySpec(
                symbol=security.symbol,
                sector=security.sector,
                beta=security.beta,
                sector_beta=security.sector_beta,
                drift=security.drift,
                process=_family_for(family, "entity", scale),
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
    ecology: EcologyProfile,
    security_count: int,
    sessions: tuple[date, ...],
    family: ProcessFamilyKind = FAMILIAR_FAMILY,
    partition: EvaluationPartition = EvaluationPartition.FAMILIAR,
) -> BenchmarkUniverse:
    """Build a deterministic synthetic universe with a chosen ecology and family."""

    if security_count < 1:
        raise ValueError("a universe needs at least one security")
    if not sessions:
        raise ValueError("a universe needs at least one session")
    rng = SemanticRNG(world_id, seed)
    sector_count = min(len(SECTORS), max(4, security_count // 4))
    sector_names = SECTORS[:sector_count]
    liquidity_cycle = ecology.liquidity_cycle

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
        family=family,
        scale=ecology.volatility_scale,
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
        partition=partition.value,
        process_family=family.value,
        ecology_label=ecology.label,
        sessions=sessions,
        securities=tuple(securities),
        market_logical_sha256=market.logical_sha256,
    )
