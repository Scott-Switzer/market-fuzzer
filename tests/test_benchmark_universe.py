from __future__ import annotations

from datetime import date

import pytest
from benchmark_worlds import benchmark_universe, day_sessions

from app.benchmark.universe import (
    DISTRIBUTION_ECOLOGY,
    FAMILIAR_ECOLOGY,
    BenchmarkUniverse,
    EcologyProfile,
    EvaluationPartition,
)
from app.market.process import FAMILIAR_FAMILY, MECHANISM_FAMILIES, ProcessFamilyKind


def _sessions(days: int = 3) -> tuple[date, ...]:
    return day_sessions(days)


def _universe(
    seed: int = 7,
    count: int = 8,
    ecology: EcologyProfile = FAMILIAR_ECOLOGY,
    family: str = FAMILIAR_FAMILY,
    partition: EvaluationPartition = EvaluationPartition.FAMILIAR,
) -> BenchmarkUniverse:
    return benchmark_universe(
        universe_id="u1",
        world_id="w1",
        seed=seed,
        ecology=ecology,
        family_id=str(family),
        partition=partition,
        security_count=count,
        sessions=_sessions(),
    )


def test_a_universe_is_deterministic_for_the_same_seed() -> None:
    first = _universe()
    second = _universe()
    assert first.market_logical_sha256 == second.market_logical_sha256
    assert first.securities == second.securities


def test_a_different_seed_yields_a_different_market() -> None:
    assert _universe(seed=7).market_logical_sha256 != _universe(seed=8).market_logical_sha256


def test_symbols_are_synthetic_and_ordered() -> None:
    universe = _universe(count=6)
    assert universe.symbols == ("SYN001", "SYN002", "SYN003", "SYN004", "SYN005", "SYN006")


def test_sector_count_is_between_four_and_eight() -> None:
    for count in (8, 16, 32, 64):
        universe = _universe(count=count)
        sectors = {security.sector for security in universe.securities}
        assert 4 <= len(sectors) <= 8


def test_every_security_has_one_price_per_session_and_positive_prices() -> None:
    universe = _universe(count=5)
    assert len(universe.sessions) == 3
    for security in universe.securities:
        assert len(security.daily_open_ticks) == 3
        assert len(security.daily_close_ticks) == 3
        assert all(value > 0 for value in security.daily_open_ticks + security.daily_close_ticks)
        assert security.initial_price_ticks > 0


def test_a_universe_requires_at_least_one_security() -> None:
    with pytest.raises(ValueError):
        _universe(count=0)


# -- ecology vs process family separation -----------------------------------------


def test_the_ecology_profile_carries_no_process_family() -> None:
    # M10.6 separates the tradable environment from the generator. An ecology must
    # not be able to name a process family or a partition.
    for profile in (FAMILIAR_ECOLOGY, DISTRIBUTION_ECOLOGY):
        fields = set(profile.__dataclass_fields__)
        assert "process_family" not in fields
        assert "family" not in fields
        assert "partition" not in fields
        assert "holdout" not in fields


def test_ecologies_differ_in_parameters_not_in_generator() -> None:
    assert DISTRIBUTION_ECOLOGY.volatility_scale > FAMILIAR_ECOLOGY.volatility_scale
    assert DISTRIBUTION_ECOLOGY.depth_scale < FAMILIAR_ECOLOGY.depth_scale
    assert DISTRIBUTION_ECOLOGY.taker_fee_bps > FAMILIAR_ECOLOGY.taker_fee_bps


def test_a_universe_records_its_partition_family_and_ecology() -> None:
    universe = _universe(
        ecology=DISTRIBUTION_ECOLOGY,
        family=ProcessFamilyKind.STOCHASTIC_VOL_FACTOR_T_V1,
        partition=EvaluationPartition.MECHANISM,
    )
    assert universe.partition == "mechanism"
    assert universe.process_family == "stochastic_vol_factor_t_v1"
    assert universe.ecology_label == DISTRIBUTION_ECOLOGY.label


def test_a_universe_carries_the_provenance_of_the_plan_that_built_it() -> None:
    universe = _universe()
    assert universe.evaluation_plan_id == "test_worlds_v1"
    assert universe.evaluation_plan_version == "v1"
    assert universe.split == "public_eval"
    # The commitment identifies the generator structurally; it is stable for a
    # given family and is what an authorized replay checks a sealed world against.
    assert len(universe.family_commitment) == 64
    assert universe.family_commitment == _universe().family_commitment
    assert universe.family_commitment == _universe(seed=99).family_commitment


def test_every_public_family_commits_to_a_distinct_generator() -> None:
    commitments = {
        family.value: _universe(family=family, partition=EvaluationPartition.MECHANISM).family_commitment
        for family in ProcessFamilyKind
    }
    assert len(set(commitments.values())) == len(ProcessFamilyKind)


def test_each_process_family_generates_a_distinct_market() -> None:
    hashes = {
        family: _universe(family=family, partition=EvaluationPartition.MECHANISM).market_logical_sha256
        for family in ProcessFamilyKind
    }
    assert len(set(hashes.values())) == len(ProcessFamilyKind)


def test_the_family_axis_is_orthogonal_to_the_ecology_axis() -> None:
    # Same ecology, different family -> different market. Same family, different
    # ecology -> different market. That is what makes the two gaps separable.
    familiar = _universe(ecology=FAMILIAR_ECOLOGY, family=FAMILIAR_FAMILY)
    shifted_ecology = _universe(ecology=DISTRIBUTION_ECOLOGY, family=FAMILIAR_FAMILY)
    shifted_family = _universe(
        ecology=DISTRIBUTION_ECOLOGY, family=MECHANISM_FAMILIES[0], partition=EvaluationPartition.MECHANISM
    )
    assert len({familiar.market_logical_sha256, shifted_ecology.market_logical_sha256}) == 2
    assert shifted_ecology.market_logical_sha256 != shifted_family.market_logical_sha256


def test_profile_rejects_negative_populations() -> None:
    with pytest.raises(ValueError):
        EcologyProfile(
            label="bad",
            volatility_scale=1.0,
            depth_scale=1.0,
            maker_fee_bps=0,
            taker_fee_bps=0,
            momentum_crowding=1.0,
            market_makers=-1,
            fundamental_traders=0,
            momentum_traders=0,
            noise_traders=0,
            liquidity_cycle=("normal",),
        )
