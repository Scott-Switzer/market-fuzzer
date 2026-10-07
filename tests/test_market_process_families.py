"""Tests for the M10.6 process-family interface and the held-out families."""

from __future__ import annotations

import math
from datetime import date

import pytest

from app.benchmark.process_registry import ProcessNodeRole, default_process_registry
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

#: The published registry is the authoritative construction path for the public
#: families, so these variance tests exercise exactly what a benchmark world uses.
_REGISTRY = default_process_registry()


def _sessions(count: int = 30) -> tuple[date, ...]:
    return tuple(trading_days(date(2020, 1, 2), date(2030, 12, 31))[:count])


def _family_for(family: ProcessFamilyKind, role: str, scale: float) -> ProcessFamily:
    return _REGISTRY.build(str(family), role=ProcessNodeRole(role), volatility_scale=scale)


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


def test_volatility_scale_multiplies_variance_by_its_square() -> None:
    for family in ProcessFamilyKind:
        base = _family_for(family, "market", 1.0).unconditional_variance()
        scaled = _family_for(family, "market", 2.0).unconditional_variance()
        assert scaled == pytest.approx(4.0 * base, rel=1e-9)


def test_reported_variance_matches_the_realized_variance() -> None:
    # The whole mechanism holdout rests on this: a family must actually generate
    # the variance it reports, at every ecology level, or the mechanism partition
    # would mix a volatility shift into the process-family effect.
    sessions = _sessions(1200)
    for scale in (1.0, 1.7):
        for role in ("market", "sector", "entity"):
            for family in ProcessFamilyKind:
                node = _family_for(family, role, scale)
                ratios = []
                for seed in range(6):
                    stream = SemanticRNG(f"w-{seed}", 9000 + seed).stream("WORLD", "market.factor")
                    path = node.innovations(stream, sessions, "market_factor")
                    mean = sum(path) / len(path)
                    realized = sum((value - mean) ** 2 for value in path) / (len(path) - 1)
                    ratios.append(realized / node.unconditional_variance())
                average = sum(ratios) / len(ratios)
                assert 0.8 < average < 1.25, (family.value, role, scale, round(average, 4))


def test_stochastic_vol_unconditional_variance_matches_the_closed_form() -> None:
    family = _sv()
    log_variance = family.sigma_eta**2 / (1.0 - family.phi**2)
    assert family.unconditional_variance() == pytest.approx(
        math.exp(family.mu + 0.5 * log_variance), rel=1e-12
    )


def test_regime_jump_innovations_are_mean_centred() -> None:
    # The raw regime-jump process drifts; the emitted innovations must not, so the
    # family axis carries no accidental drift shift relative to zero-mean families.
    family = _mrj()
    assert family.stationary_mean() != 0.0
    stream = SemanticRNG("w-centred", 5).stream("WORLD", "market.factor")
    path = family.innovations(stream, _sessions(4000), "market_factor")
    assert abs(sum(path) / len(path)) < 1e-3


def test_regime_jump_starts_in_the_stationary_regime_distribution() -> None:
    # The initial regime is drawn from the stationary distribution (0.8 / 0.2),
    # not uniformly (0.5 / 0.5), so the first session already has the variance the
    # family reports.
    family = _mrj()
    assert family.stationary_distribution() == pytest.approx((0.8, 0.2))
    starts = [family._stationary_regime(step / 1000) for step in range(1000)]
    assert starts.count(0) == 800
    assert starts.count(1) == 200


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
        StochasticVolFactorT(mu=0.0, phi=0.9, sigma_eta=0.1, nu=5.0, volatility_scale=0.0)


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
