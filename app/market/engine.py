"""Deterministic daily market engine (Milestone 10).

This is the first slice of the M10 daily market: a close-price path driven by a
cross-sectional factor structure whose innovations come from a pluggable
*process family* (M10.5 shipped GJR-GARCH-t; M10.6 adds stochastic-volatility and
regime-jump families in :mod:`app.market.process`), and Brownian-bridge OHLC bars
whose bound invariants are enforced at construction.

:class:`ProcessFamily` is the seam: every node (market factor, sector factor,
company idiosyncratic) exposes ``innovations(stream, sessions, variable)``, so
the engine's factor arithmetic is identical for every generator family.

Determinism rests on two things:

* ``app.world.rng.SemanticRNG`` addresses every draw by
  ``(world, entity, mechanism, variable, distribution)`` + ordinal, so the same
  seed/entity/date/mechanism reproduces a draw and a change confined to an
  unrelated mechanism cannot shift an existing stream.
* no mutable generator state is used anywhere in this module.

Event jumps are supplied per ``(symbol, session)`` and are declared as an
intervenable variable in :mod:`app.market.registry`, so a counterfactual twin
that clamps one company's jumps leaves every non-descendant series byte-identical.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import ClassVar

from app.market.process import GJR_FACTOR_T_V1, ProcessFamily
from app.world.rng import SemanticStream

__all__ = [
    "Bar",
    "EntitySpec",
    "GjrGarchT",
    "MarketResult",
    "MarketWorld",
    "OhlcBoundError",
    "ProcessFamily",
    "SimulatedSeries",
    "brownian_bridge_bar",
    "logical_sha256",
    "simulate_daily_market",
    "standardized_t_draw",
    "validate_bar",
]


class OhlcBoundError(ValueError):
    """A bar violates the OHLC bound invariants (rule ``MKT-OHLC-BOUNDS``)."""


@dataclass(frozen=True)
class GjrGarchT(ProcessFamily):
    """GJR-GARCH-t parameters -- the ``gjr_factor_t_v1`` process family.

    ``sigma2_t = omega + alpha*eps2_{t-1} + gamma*I[eps_{t-1}<0]*eps2_{t-1}
    + beta*sigma2_{t-1}`` with unit-variance Student-t innovations.
    """

    omega: float
    alpha: float
    gamma: float
    beta: float
    nu: float

    name: ClassVar[str] = GJR_FACTOR_T_V1

    def __post_init__(self) -> None:
        if self.omega <= 0:
            raise ValueError("omega must be positive")
        if self.alpha < 0 or self.gamma < 0 or self.beta < 0:
            raise ValueError("alpha, gamma and beta must be non-negative")
        if self.nu <= 2:
            raise ValueError("nu must exceed 2 for a finite variance")
        if self.persistence >= 1.0:
            raise ValueError("process is not covariance-stationary")

    @property
    def persistence(self) -> float:
        """alpha + gamma/2 + beta (the symmetric-innovation persistence)."""

        return self.alpha + 0.5 * self.gamma + self.beta

    def unconditional_variance(self) -> float:
        return self.omega / (1.0 - self.persistence)

    def innovations(
        self, stream: SemanticStream, sessions: Sequence[date], variable: str
    ) -> tuple[float, ...]:
        """Per-session innovations for the GJR-GARCH-t recursion."""

        return tuple(_gjr_garch_t_path(stream, self, sessions, variable))


def _chi_square_draw(stream: SemanticStream, variable: str, ordinal: int, nu: float) -> float:
    """Deterministic chi-square draw with ``nu`` degrees of freedom."""

    return 2.0 * _gamma_draw(stream, variable, ordinal, nu / 2.0)


def _gamma_draw(stream: SemanticStream, variable: str, ordinal: int, shape: float) -> float:
    """Marsaglia-Tsang gamma sampler over semantic draws (scale 1)."""

    if shape < 1.0:
        boost = stream.uniform(f"{variable}.boost", ordinal, 0)
        return _gamma_draw(stream, variable, ordinal, shape + 1.0) * boost ** (1.0 / shape)
    d = shape - 1.0 / 3.0
    c = 1.0 / math.sqrt(9.0 * d)
    index = 0
    while True:
        x = stream.normal(f"{variable}.x", ordinal, index)
        index += 1
        v = (1.0 + c * x) ** 3
        if v <= 0.0:
            continue
        u = stream.uniform(f"{variable}.u", ordinal, index)
        index += 1
        if u < 1.0 - 0.0331 * x**4:
            return d * v
        if math.log(u) < 0.5 * x * x + d * (1.0 - v + math.log(v)):
            return d * v


def standardized_t_draw(stream: SemanticStream, variable: str, ordinal: int, nu: float) -> float:
    """One unit-variance Student-t draw (variance = nu / (nu - 2))."""

    if nu <= 2:
        raise ValueError("nu must exceed 2")
    z = stream.normal(f"{variable}.z", ordinal, 0)
    chi2 = _chi_square_draw(stream, f"{variable}.chi2", ordinal, nu)
    return z * math.sqrt((nu - 2.0) / chi2)


def _gjr_garch_t_path(
    stream: SemanticStream, params: GjrGarchT, sessions: Sequence[date], variable: str
) -> list[float]:
    """Per-session innovations ``eps_t = sigma_t * z_t`` for one semantic stream."""

    unconditional = params.unconditional_variance()
    returns: list[float] = []
    previous_eps = 0.0
    previous_sigma2 = unconditional
    for ordinal in range(len(sessions)):
        if ordinal == 0:
            sigma2 = unconditional
        else:
            shock2 = previous_eps * previous_eps
            asymmetry = shock2 if previous_eps < 0.0 else 0.0
            sigma2 = (
                params.omega
                + params.alpha * shock2
                + params.gamma * asymmetry
                + params.beta * previous_sigma2
            )
        z = standardized_t_draw(stream, variable, ordinal, params.nu)
        epsilon = math.sqrt(sigma2) * z
        returns.append(epsilon)
        previous_eps = epsilon
        previous_sigma2 = sigma2
    return returns


@dataclass(frozen=True)
class Bar:
    """One daily OHLC bar (prices > 0; low <= min(open, close) <= max(open, close) <= high)."""

    symbol: str
    session: date
    open: float
    high: float
    low: float
    close: float


def validate_bar(bar: Bar) -> None:
    """Raise :class:`OhlcBoundError` if a bar violates ``MKT-OHLC-BOUNDS``."""

    prices = {"open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close}
    for name, value in prices.items():
        if not math.isfinite(value) or value <= 0.0:
            raise OhlcBoundError(f"{bar.symbol}@{bar.session} {name}={value!r} is not a positive price")
    if bar.low > min(bar.open, bar.close):
        raise OhlcBoundError(f"{bar.symbol}@{bar.session} low={bar.low!r} exceeds min(open, close)")
    if bar.high < max(bar.open, bar.close):
        raise OhlcBoundError(f"{bar.symbol}@{bar.session} high={bar.high!r} is below max(open, close)")
    if bar.high < bar.low:
        raise OhlcBoundError(f"{bar.symbol}@{bar.session} high={bar.high!r} < low={bar.low!r}")


def brownian_bridge_bar(
    *,
    symbol: str,
    session: date,
    previous_close: float,
    close: float,
    stream: SemanticStream,
    ordinal: int,
    sigma_open: float,
    sigma_range: float,
) -> Bar:
    """An OHLC bar whose open gaps and whose range spans both open and close.

    The bridge is multiplicative, so ``high >= max(open, close)`` and
    ``low <= min(open, close)`` hold by construction; :func:`validate_bar` still
    runs so a regression cannot silently violate the bound.
    """

    if previous_close <= 0.0 or close <= 0.0:
        raise OhlcBoundError("prices must be positive")
    open_price = previous_close * math.exp(sigma_open * stream.normal("ohlc.open", ordinal, 0))
    high = max(open_price, close) * math.exp(sigma_range * abs(stream.normal("ohlc.high", ordinal, 0)))
    low = min(open_price, close) * math.exp(-sigma_range * abs(stream.normal("ohlc.low", ordinal, 0)))
    bar = Bar(symbol=symbol, session=session, open=open_price, high=high, low=low, close=close)
    validate_bar(bar)
    return bar


@dataclass(frozen=True)
class EntitySpec:
    """One tradable entity, its factor loadings, and its idiosyncratic family."""

    symbol: str
    sector: str
    beta: float
    sector_beta: float
    drift: float
    process: ProcessFamily


@dataclass(frozen=True)
class MarketWorld:
    """A complete daily-market specification. Everything the output depends on is here."""

    world_id: str
    seed: int
    sessions: tuple[date, ...]
    market: ProcessFamily
    sectors: Mapping[str, ProcessFamily]
    entities: tuple[EntitySpec, ...]
    jumps: Mapping[str, Mapping[date, float]] = field(default_factory=dict)
    sigma_open: float = 0.004
    sigma_range: float = 0.006
    start_close: float = 100.0


@dataclass(frozen=True)
class SimulatedSeries:
    symbol: str
    closes: tuple[float, ...]
    bars: tuple[Bar, ...]


@dataclass(frozen=True)
class MarketResult:
    world_id: str
    seed: int
    sessions: tuple[date, ...]
    market_factor: tuple[float, ...]
    sector_factors: Mapping[str, tuple[float, ...]]
    series: Mapping[str, SimulatedSeries]
    logical_sha256: str


def _payload(result: MarketResult) -> dict[str, object]:
    return {
        "world_id": result.world_id,
        "seed": result.seed,
        "sessions": [s.isoformat() for s in result.sessions],
        "market_factor": [repr(v) for v in result.market_factor],
        "sector_factors": {
            name: [repr(v) for v in values] for name, values in sorted(result.sector_factors.items())
        },
        "series": {
            symbol: {
                "closes": [repr(v) for v in series.closes],
                "bars": [
                    {
                        "session": bar.session.isoformat(),
                        "open": repr(bar.open),
                        "high": repr(bar.high),
                        "low": repr(bar.low),
                        "close": repr(bar.close),
                    }
                    for bar in series.bars
                ],
            }
            for symbol, series in sorted(result.series.items())
        },
    }


def logical_sha256(result: MarketResult) -> str:
    """Content hash of the market output, independent of insertion order."""

    canonical = json.dumps(_payload(result), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def simulate_daily_market(world: MarketWorld) -> MarketResult:
    """Simulate a deterministic daily market world."""

    from app.world.rng import SemanticRNG

    if not world.sessions:
        raise ValueError("a market world needs at least one session")
    rng = SemanticRNG(world.world_id, world.seed)

    market_stream = rng.stream("WORLD", "market.factor")
    market_factor = list(world.market.innovations(market_stream, world.sessions, "market_factor"))

    sector_factors: dict[str, tuple[float, ...]] = {}
    for sector in sorted(world.sectors):
        stream = rng.stream(f"SECTOR:{sector}", "sector.factor")
        sector_factors[sector] = world.sectors[sector].innovations(stream, world.sessions, "sector_factor")

    series: dict[str, SimulatedSeries] = {}
    for entity in world.entities:
        if entity.sector not in sector_factors:
            raise ValueError(f"{entity.symbol} references unknown sector {entity.sector!r}")
        idio_stream = rng.stream(f"COMPANY:{entity.symbol}", "market.idiosyncratic")
        idio = entity.process.innovations(idio_stream, world.sessions, "idiosyncratic")
        ohlc_stream = rng.stream(f"COMPANY:{entity.symbol}", "market.ohlc")
        jumps = world.jumps.get(entity.symbol, {})
        closes: list[float] = []
        bars: list[Bar] = []
        previous_close = world.start_close
        sector_series = sector_factors[entity.sector]
        for ordinal, session in enumerate(world.sessions):
            log_return = (
                entity.drift
                + entity.beta * market_factor[ordinal]
                + entity.sector_beta * sector_series[ordinal]
                + idio[ordinal]
                + float(jumps.get(session, 0.0))
            )
            close = previous_close * math.exp(log_return)
            bar = brownian_bridge_bar(
                symbol=entity.symbol,
                session=session,
                previous_close=previous_close,
                close=close,
                stream=ohlc_stream,
                ordinal=ordinal,
                sigma_open=world.sigma_open,
                sigma_range=world.sigma_range,
            )
            bars.append(bar)
            closes.append(close)
            previous_close = close
        series[entity.symbol] = SimulatedSeries(symbol=entity.symbol, closes=tuple(closes), bars=tuple(bars))

    result = MarketResult(
        world_id=world.world_id,
        seed=world.seed,
        sessions=world.sessions,
        market_factor=tuple(market_factor),
        sector_factors=sector_factors,
        series=series,
        logical_sha256="",
    )
    result = MarketResult(
        world_id=result.world_id,
        seed=result.seed,
        sessions=result.sessions,
        market_factor=result.market_factor,
        sector_factors=result.sector_factors,
        series=result.series,
        logical_sha256=logical_sha256(result),
    )
    return result
