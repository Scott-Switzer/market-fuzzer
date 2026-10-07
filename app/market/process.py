"""Process-family interface and the M10.6 generator families.

A *process family* is the stochastic mechanism that generates the per-session
log-return innovations of one node (the market factor, a sector factor, or a
single company). M10.5 shipped exactly one family, GJR-GARCH-t. M10.6 adds a
common :class:`ProcessFamily` interface and two additional families so the
benchmark can measure a *market-process generalization gap*:

* ``gjr_factor_t_v1``               GJR-GARCH-t (the *familiar* family; the
  implementation lives on :class:`app.market.engine.GjrGarchT`).
* ``stochastic_vol_factor_t_v1``    log-normal stochastic volatility (an AR(1)
  latent log-variance) with unit-variance Student-t innovations.
* ``markov_regime_jump_factor_t_v1`` a hidden two-state Markov chain with
  regime-specific drift and volatility plus compound-Poisson jumps whose size is
  regime dependent.

The families differ *structurally* -- in how volatility clusters, how tails are
generated, and whether the distribution switches between regimes -- while their
unconditional per-session variance is matched by construction. That keeps the
*ecology* axis (volatility level) orthogonal to the *family* axis (dynamics), so
a score drop on the mechanism partition reflects the process family rather than a
simple scaling difference.

Families are immutable and draw only from the keyed
:class:`~app.world.rng.SemanticStream`, so every draw is addressed by
``(world, entity, mechanism, variable, period)``. A change confined to one family
cannot perturb another family's stream and the world stays byte-deterministic.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import ClassVar

from app.world.rng import SemanticStream

__all__ = [
    "FAMILIAR_FAMILY",
    "GJR_FACTOR_T_V1",
    "MARKOV_REGIME_JUMP_FACTOR_T_V1",
    "MECHANISM_FAMILIES",
    "MarkovRegimeJumpFactorT",
    "ProcessFamily",
    "ProcessFamilyKind",
    "STOCHASTIC_VOL_FACTOR_T_V1",
    "StochasticVolFactorT",
]

GJR_FACTOR_T_V1 = "gjr_factor_t_v1"
STOCHASTIC_VOL_FACTOR_T_V1 = "stochastic_vol_factor_t_v1"
MARKOV_REGIME_JUMP_FACTOR_T_V1 = "markov_regime_jump_factor_t_v1"


class ProcessFamilyKind(StrEnum):
    """The three process families the M10.6 benchmark can generate."""

    GJR_FACTOR_T_V1 = GJR_FACTOR_T_V1
    STOCHASTIC_VOL_FACTOR_T_V1 = STOCHASTIC_VOL_FACTOR_T_V1
    MARKOV_REGIME_JUMP_FACTOR_T_V1 = MARKOV_REGIME_JUMP_FACTOR_T_V1


#: The family every familiar-partition world uses.
FAMILIAR_FAMILY = ProcessFamilyKind.GJR_FACTOR_T_V1
#: Families reserved for the mechanism partition; the agent never sees them.
MECHANISM_FAMILIES: tuple[ProcessFamilyKind, ...] = (
    ProcessFamilyKind.STOCHASTIC_VOL_FACTOR_T_V1,
    ProcessFamilyKind.MARKOV_REGIME_JUMP_FACTOR_T_V1,
)


class ProcessFamily(ABC):
    """The common interface every generator family implements.

    ``name`` is the stable evaluator-private identifier recorded in the replay
    package. ``innovations`` returns one log-return innovation per session for a
    single node.
    """

    name: ClassVar[str]

    @abstractmethod
    def innovations(
        self, stream: SemanticStream, sessions: Sequence[date], variable: str
    ) -> tuple[float, ...]:
        """Per-session log-return innovations ``eps_t`` for one node."""

    @abstractmethod
    def unconditional_variance(self) -> float:
        """The stationary per-session variance of the innovations."""


@dataclass(frozen=True)
class StochasticVolFactorT(ProcessFamily):
    """Log-normal stochastic volatility with unit-variance Student-t shocks.

    ``h_t = mu + phi * (h_{t-1} - mu) + sigma_eta * eta_t`` is the latent log
    variance and ``eps_t = exp(h_t / 2) * z_t`` with ``z_t`` unit-variance
    Student-t. Volatility therefore has its own shock source instead of being a
    function of past squared returns, which is the structural difference from the
    GJR-GARCH-t family.
    """

    mu: float
    phi: float
    sigma_eta: float
    nu: float
    variance_scale: float = 1.0

    name: ClassVar[str] = STOCHASTIC_VOL_FACTOR_T_V1

    def __post_init__(self) -> None:
        if not -1.0 < self.phi < 1.0:
            raise ValueError("phi must lie strictly inside (-1, 1)")
        if self.sigma_eta <= 0.0:
            raise ValueError("sigma_eta must be positive")
        if self.nu <= 2.0:
            raise ValueError("nu must exceed 2 for a finite variance")
        if self.variance_scale <= 0.0:
            raise ValueError("variance_scale must be positive")

    @property
    def log_variance(self) -> float:
        """Stationary variance of the latent log-volatility process."""

        return self.sigma_eta**2 / (1.0 - self.phi**2)

    def unconditional_variance(self) -> float:
        return self.variance_scale**2 * math.exp(self.mu + 0.5 * self.log_variance)

    def innovations(
        self, stream: SemanticStream, sessions: Sequence[date], variable: str
    ) -> tuple[float, ...]:
        # Imported lazily so engine.py can import this module without a cycle.
        from app.market.engine import standardized_t_draw

        scale = math.sqrt(self.variance_scale)
        level = self.mu + self.sigma_eta * math.sqrt(self.log_variance) * stream.normal(
            f"{variable}.sv.level", 0, 0
        )
        innovations: list[float] = []
        for ordinal in range(len(sessions)):
            if ordinal > 0:
                level = (
                    self.mu
                    + self.phi * (level - self.mu)
                    + self.sigma_eta * stream.normal(f"{variable}.sv.level", ordinal, 0)
                )
            shock = standardized_t_draw(stream, f"{variable}.sv", ordinal, self.nu)
            innovations.append(scale * math.exp(0.5 * level) * shock)
        return tuple(innovations)


@dataclass(frozen=True)
class MarkovRegimeJumpFactorT(ProcessFamily):
    """A hidden Markov chain with regime drift/volatility and Poisson jumps.

    Each session's innovation is ``mean(state) + sigma(state) * z_t`` plus, with
    probability ``jump_prob``, a normal jump scaled by ``jump_scale(state)``.
    The regime therefore changes both the drift and the volatility, and stressed
    regimes jump harder -- a structure none of the other families can express.
    """

    means: tuple[float, ...]
    sigmas: tuple[float, ...]
    jump_scale: tuple[float, ...]
    transition: tuple[tuple[float, ...], ...]
    jump_prob: float
    jump_mean: float
    jump_sigma: float
    nu: float
    variance_scale: float = 1.0

    name: ClassVar[str] = MARKOV_REGIME_JUMP_FACTOR_T_V1

    def __post_init__(self) -> None:
        regimes = len(self.means)
        if regimes < 2:
            raise ValueError("a regime-jump family needs at least two regimes")
        if len(self.sigmas) != regimes or len(self.jump_scale) != regimes:
            raise ValueError("sigmas and jump_scale must match the regime count")
        if len(self.transition) != regimes or any(len(row) != regimes for row in self.transition):
            raise ValueError("transition must be a square regime_count by regime_count matrix")
        if any(value <= 0.0 for value in self.sigmas):
            raise ValueError("regime volatilities must be positive")
        if any(value < 0.0 for value in self.jump_scale):
            raise ValueError("jump scales must be non-negative")
        for row in self.transition:
            if any(value < 0.0 for value in row):
                raise ValueError("transition probabilities must be non-negative")
            if abs(sum(row) - 1.0) > 1e-9:
                raise ValueError("each transition row must sum to one")
        if not 0.0 <= self.jump_prob <= 1.0:
            raise ValueError("jump_prob must lie in [0, 1]")
        if self.jump_sigma < 0.0:
            raise ValueError("jump_sigma must be non-negative")
        if self.nu <= 2.0:
            raise ValueError("nu must exceed 2 for a finite variance")
        if self.variance_scale <= 0.0:
            raise ValueError("variance_scale must be positive")

    @property
    def regime_count(self) -> int:
        return len(self.means)

    def stationary_distribution(self) -> tuple[float, ...]:
        """Stationary regime probabilities via deterministic power iteration."""

        count = self.regime_count
        probabilities = [1.0 / count] * count
        for _ in range(256):
            updated = [0.0] * count
            for source, probability in enumerate(probabilities):
                for target, step in enumerate(self.transition[source]):
                    updated[target] += probability * step
            probabilities = updated
        total = sum(probabilities)
        return tuple(probability / total for probability in probabilities)

    def unconditional_variance(self) -> float:
        probabilities = self.stationary_distribution()
        regime = sum(
            probability * (self.sigmas[index] ** 2 + self.means[index] ** 2)
            for index, probability in enumerate(probabilities)
        )
        jump = (
            self.jump_prob
            * (self.jump_mean**2 + self.jump_sigma**2)
            * sum(
                probability * self.jump_scale[index] ** 2 for index, probability in enumerate(probabilities)
            )
        )
        return self.variance_scale**2 * (regime + jump)

    def _next_regime(self, regime: int, draw: float) -> int:
        cumulative = 0.0
        row = self.transition[regime]
        for target, step in enumerate(row):
            cumulative += step
            if draw < cumulative:
                return target
        return len(row) - 1

    def innovations(
        self, stream: SemanticStream, sessions: Sequence[date], variable: str
    ) -> tuple[float, ...]:
        # Imported lazily so engine.py can import this module without a cycle.
        from app.market.engine import standardized_t_draw

        scale = math.sqrt(self.variance_scale)
        regime = min(
            self.regime_count - 1, int(stream.uniform(f"{variable}.mrj.init", 0, 0) * self.regime_count)
        )
        innovations: list[float] = []
        for ordinal in range(len(sessions)):
            if ordinal > 0:
                regime = self._next_regime(regime, stream.uniform(f"{variable}.mrj.state", ordinal, 0))
            shock = standardized_t_draw(stream, f"{variable}.mrj", ordinal, self.nu)
            value = self.means[regime] + self.sigmas[regime] * shock
            if stream.uniform(f"{variable}.mrj.jump", ordinal, 0) < self.jump_prob:
                jump = self.jump_mean + self.jump_sigma * stream.normal(
                    f"{variable}.mrj.jump_size", ordinal, 0
                )
                value += self.jump_scale[regime] * jump
            innovations.append(scale * value)
        return tuple(innovations)
