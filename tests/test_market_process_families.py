"""Tests for the M10.6 process-family interface and the held-out families."""

from __future__ import annotations

import math
from datetime import date

import pytest

from app.benchmark.universe import _family_for
from app.market.calendar import trading_days
from app.market.engine import GjrGarchT
from app.market.process import (
    FAMILIAR_FAMILY,
    GJR_FACTOR_T_V1,
    MARKOV_REGIME_JUMP_FACTOR_T_V1,
    MECHANISM_FAMILIES,
    STOCHASTIC_VOL_FACTOR_T_V1,
    MarkovRegimeJumpFactorT,
    ProcessFamily,
    ProcessFamilyKind,
    StochasticVolFactorT,
)
from app.world.rng import SemanticRNG


def _sessions(count: int = 30) -> tuple[date, ...]:
    return tuple(trading_days(date(2026, 6, 1), date(2026, 9, 30))[:count])


def _sv() -> StochasticVolFactorT:
    return StochasticVolFactorT(mu=-9.0, phi=0.95, sigma_eta=0.15, nu=5.0)


def _mrj() -> MarkovRegimeJumpFactorT:
    return MarkovRegimeJumpFactorT(
        means=(0.0006, -0.0015),
        sigmas=(0.006, 0.018),
        jump_scale=(0.4, 2.0),
        transition=((0.95, 0.05), (0.20, 0.80)),
        jump_prob=0.02,
        jump_mean=-0.0002,
        jump_sigma=0.02,
        nu=5.0,
    )


# -- interface -------------------------------------------------------------------


def test_every_family_satisfies_the_common_interface() -> None:
    for family in (GjrGarchT(0.02, 0.05, 0.1, 0.8, 6.0), _sv(), _mrj()):
        assert isinstance(family, ProcessFamily)
        assert isinstance(family.name, str) and family.name
        assert family.unconditional_variance() > 0.0


def test_the_three_family_names_are_distinct() -> None:
    names = {family.value for family in ProcessFamilyKind}
    assert names == {GJR_FACTOR_T_V1, STOCHASTIC_VOL_FACTOR_T_V1, MARKOV_REGIME_JUMP_FACTOR_T_V1}
    assert FAMILIAR_FAMILY not in MECHANISM_FAMILIES
    assert set(MECHANISM_FAMILIES) <= set(ProcessFamilyKind)


@pytest.mark.parametrize(
    "family",
    [GjrGarchT(0.02, 0.05, 0.1, 0.8, 6.0), _sv(), _mrj()],
    ids=["gjr", "sv", "mrj"],
)
def test_each_family_yields_one_finite_innovation_per_session(family: ProcessFamily) -> None:
    sessions = _sessions(25)
    stream = SemanticRNG("w", 11).stream("WORLD", "market.factor")
    path = family.innovations(stream, sessions, "market_factor")
    assert len(path) == len(sessions)
    assert all(math.isfinite(value) for value in path)
    assert any(value != 0.0 for value in path)


@pytest.mark.parametrize(
    "family",
    [GjrGarchT(0.02, 0.05, 0.1, 0.8, 6.0), _sv(), _mrj()],
    ids=["gjr", "sv", "mrj"],
)
def test_each_family_is_deterministic_and_seed_sensitive(family: ProcessFamily) -> None:
    sessions = _sessions(20)
    first = family.innovations(SemanticRNG("w", 7).stream("WORLD", "m"), sessions, "f")
    second = family.innovations(SemanticRNG("w", 7).stream("WORLD", "m"), sessions, "f")
    other = family.innovations(SemanticRNG("w", 8).stream("WORLD", "m"), sessions, "f")
    assert first == second
    assert first != other


def test_the_families_generate_different_paths_from_the_same_seed() -> None:
    sessions = _sessions(20)
    paths = [
        family.innovations(SemanticRNG("w", 7).stream("WORLD", "m"), sessions, "f")
        for family in (GjrGarchT(0.02, 0.05, 0.1, 0.8, 6.0), _sv(), _mrj())
    ]
    assert len({tuple(path) for path in paths}) == 3


# -- variance normalization --------------------------------------------------------


def test_every_family_is_normalized_to_the_same_unconditional_variance() -> None:
    for role in ("market", "sector", "entity"):
        variances = [_family_for(family, role, 1.0).unconditional_variance() for family in ProcessFamilyKind]
        assert max(variances) / min(variances) == pytest.approx(1.0, rel=1e-9)


def test_variance_scale_is_a_pure_scale_that_preserves_dynamics() -> None:
    for family in ProcessFamilyKind:
        base = _family_for(family, "market", 1.0).unconditional_variance()
        scaled = _family_for(family, "market", 2.0).unconditional_variance()
        assert scaled == pytest.approx(4.0 * base, rel=1e-9)


def test_stochastic_vol_unconditional_variance_matches_the_closed_form() -> None:
    family = _sv()
    log_variance = family.sigma_eta**2 / (1.0 - family.phi**2)
    assert family.unconditional_variance() == pytest.approx(
        math.exp(family.mu + 0.5 * log_variance), rel=1e-12
    )


def test_regime_stationary_distribution_is_solved_correctly() -> None:
    family = MarkovRegimeJumpFactorT(
        means=(0.0, 0.0),
        sigmas=(0.01, 0.01),
        jump_scale=(0.0, 0.0),
        transition=((0.9, 0.1), (0.3, 0.7)),
        jump_prob=0.0,
        jump_mean=0.0,
        jump_sigma=0.0,
        nu=5.0,
    )
    stationary = family.stationary_distribution()
    assert stationary[0] == pytest.approx(0.75, abs=1e-9)
    assert stationary[1] == pytest.approx(0.25, abs=1e-9)


# -- validation --------------------------------------------------------------------


def test_stochastic_vol_rejects_invalid_parameters() -> None:
    with pytest.raises(ValueError):
        StochasticVolFactorT(mu=0.0, phi=1.0, sigma_eta=0.1, nu=5.0)
    with pytest.raises(ValueError):
        StochasticVolFactorT(mu=0.0, phi=0.9, sigma_eta=0.0, nu=5.0)
    with pytest.raises(ValueError):
        StochasticVolFactorT(mu=0.0, phi=0.9, sigma_eta=0.1, nu=2.0)
    with pytest.raises(ValueError):
        StochasticVolFactorT(mu=0.0, phi=0.9, sigma_eta=0.1, nu=5.0, variance_scale=0.0)


def test_regime_jump_rejects_invalid_parameters() -> None:
    good = _mrj()
    with pytest.raises(ValueError):
        MarkovRegimeJumpFactorT(
            means=(0.0,),
            sigmas=(0.01,),
            jump_scale=(1.0,),
            transition=((1.0,),),
            jump_prob=0.0,
            jump_mean=0.0,
            jump_sigma=0.0,
            nu=5.0,
        )
    with pytest.raises(ValueError):
        MarkovRegimeJumpFactorT(
            means=good.means,
            sigmas=good.sigmas,
            jump_scale=good.jump_scale,
            transition=((0.5, 0.4), (0.2, 0.8)),  # rows must sum to one
            jump_prob=0.0,
            jump_mean=0.0,
            jump_sigma=0.0,
            nu=5.0,
        )
    with pytest.raises(ValueError):
        MarkovRegimeJumpFactorT(
            means=good.means,
            sigmas=good.sigmas,
            jump_scale=good.jump_scale,
            transition=good.transition,
            jump_prob=1.5,
            jump_mean=0.0,
            jump_sigma=0.0,
            nu=5.0,
        )
