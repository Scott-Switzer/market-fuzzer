"""Synthetic securities universe for the exchange benchmark (M10.6.1).

The universe is built from the M10 daily market engine: a cross-sectional factor
structure whose innovations come from a selectable *process family*. The engine
produces daily OHLC paths for every synthetic company; the intraday
limit-order-book session then emerges from the background agents around those
fundamental anchors.

M10.6 separated the two axes that M10.5 conflated:

* the **process family** -- how innovations are generated (GJR-GARCH-t,
  stochastic volatility, or regime-jump). See :mod:`app.market.process`.
* the **ecology** -- the tradable environment around the same price process:
  volatility level, displayed depth, fee schedule, and background-agent mix
  (:class:`EcologyProfile`).

M10.6.1 removes the last hard-coded link between the two: a universe is built from
a resolved :class:`~app.benchmark.plan.PlannedWorld` and a
:class:`~app.benchmark.process_registry.MarketProcessRegistry`, so the family is
looked up by identifier instead of being restricted to a public enum. The role
parameters and the variance normalization for the built-in families now live with
their definitions in :mod:`app.benchmark.process_registry`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from app.benchmark.process_registry import MarketProcessRegistry, ProcessNodeRole
from app.market.engine import EntitySpec, MarketWorld, simulate_daily_market
from app.world.rng import SemanticRNG

if TYPE_CHECKING:  # pragma: no cover - import-cycle guard, never executed at runtime
    from app.benchmark.plan import PlannedWorld

__all__ = [
    "DISTRIBUTION_ECOLOGY",
    "EvaluationPartition",
    "EcologyProfile",
    "FAMILIAR_ECOLOGY",
    "HIDDEN_PROFILE",
    "PUBLIC_PROFILE",
    "BenchmarkUniverse",
    "LiquidityRegime",
    "Security",
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
    """The world roles a plan assigns.

    ``familiar``, ``distribution``, and ``mechanism`` are the M10.6 evaluation
    partitions. M10.7 adds ``training``: a *training* world is not an evaluation
    world at all -- it is generated for a training corpus, and both the evaluator
    and the corpus builder refuse the other's partition/split combination.
    """

    FAMILIAR = "familiar"
    DISTRIBUTION = "distribution"
    MECHANISM = "mechanism"
    TRAINING = "training"


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


@dataclass(frozen=True, slots=True)
class Security:
    """One synthetic security and its generated daily fundamentals.

    The intraday mark ladder interpolates open->close per day. M10.7 additionally
    retails the engine's real daily high/low ticks so a training corpus can emit
    honest OHLC bars; they are *not* used by the session, so world behaviour in
    M10.5-M10.6.1 is unchanged.
    """

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
    daily_high_ticks: tuple[int, ...] = ()
    daily_low_ticks: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class BenchmarkUniverse:
    """A generated set of synthetic securities over a fixed session calendar.

    Carries the full evaluator-side provenance of the world it materializes: which
    plan produced it, which partition it belongs to, which family generated it,
    and the family's commitment digest. None of it is agent-visible; the session
    only ever hands the agent quotes, its own order state, and the task.
    """

    universe_id: str
    world_id: str
    seed: int
    partition: str
    process_family: str
    family_commitment: str
    evaluation_plan_id: str
    evaluation_plan_version: str
    split: str
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
    family_id: str,
    registry: MarketProcessRegistry,
    scale: float,
) -> MarketWorld:
    sectors = sorted({security.sector for security in securities})
    return MarketWorld(
        world_id=world_id,
        seed=seed,
        sessions=sessions,
        market=registry.build(family_id, role=ProcessNodeRole.MARKET, volatility_scale=scale),
        sectors={
            sector: registry.build(family_id, role=ProcessNodeRole.SECTOR, volatility_scale=scale)
            for sector in sectors
        },
        entities=tuple(
            EntitySpec(
                symbol=security.symbol,
                sector=security.sector,
                beta=security.beta,
                sector_beta=security.sector_beta,
                drift=security.drift,
                process=registry.build(family_id, role=ProcessNodeRole.ENTITY, volatility_scale=scale),
            )
            for security in securities
        ),
    )


def _scaled_ticks(price_ticks: float, ratio: float) -> int:
    return max(1, round(price_ticks * ratio))


def build_universe(
    *,
    universe_id: str,
    planned: PlannedWorld,
    registry: MarketProcessRegistry,
    security_count: int,
    sessions: tuple[date, ...],
) -> BenchmarkUniverse:
    """Materialize one planned world into a deterministic synthetic universe.

    ``planned`` supplies the world identity, the seed, the ecology, the partition,
    the process family, and the plan provenance; ``registry`` supplies the family
    implementation. The market path depends only on those values, so the same
    plan, family, and seed reproduce the same securities byte for byte.
    """

    if security_count < 1:
        raise ValueError("a universe needs at least one security")
    if not sessions:
        raise ValueError("a universe needs at least one session")
    ecology = planned.ecology
    rng = SemanticRNG(planned.world_id, planned.seed)
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
        world_id=planned.world_id,
        seed=planned.seed,
        sessions=sessions,
        securities=provisional,
        family_id=planned.family_id,
        registry=registry,
        scale=ecology.volatility_scale,
    )
    market = simulate_daily_market(market_world)

    securities: list[Security] = []
    for security in provisional:
        series = market.series[security.symbol]
        base = float(security.initial_price_ticks)
        opens = tuple(_scaled_ticks(base, bar.open / 100.0) for bar in series.bars)
        closes = tuple(_scaled_ticks(base, bar.close / 100.0) for bar in series.bars)
        highs = tuple(_scaled_ticks(base, bar.high / 100.0) for bar in series.bars)
        lows = tuple(_scaled_ticks(base, bar.low / 100.0) for bar in series.bars)
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
                daily_high_ticks=highs,
                daily_low_ticks=lows,
            )
        )

    return BenchmarkUniverse(
        universe_id=universe_id,
        world_id=planned.world_id,
        seed=planned.seed,
        partition=planned.partition.value,
        process_family=planned.family_id,
        family_commitment=planned.family_commitment,
        evaluation_plan_id=planned.plan_id,
        evaluation_plan_version=planned.plan_version,
        split=planned.split.value,
        ecology_label=ecology.label,
        sessions=sessions,
        securities=tuple(securities),
        market_logical_sha256=market.logical_sha256,
    )
