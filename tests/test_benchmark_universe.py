from __future__ import annotations

from datetime import date

import pytest

from app.benchmark.universe import (
    HIDDEN_PROFILE,
    PUBLIC_PROFILE,
    HoldoutProfile,
    build_universe,
)
from app.market.calendar import trading_days


def _sessions(days: int = 3) -> tuple[date, ...]:
    return tuple(trading_days(date(2026, 6, 1), date(2026, 6, 30))[:days])


def _universe(seed: int = 7, count: int = 8, profile: HoldoutProfile = PUBLIC_PROFILE):
    return build_universe(
        universe_id="u1",
        world_id="w1",
        seed=seed,
        profile=profile,
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


def test_holdout_profiles_differ_in_mechanism_and_market() -> None:
    assert PUBLIC_PROFILE.holdout == "public"
    assert HIDDEN_PROFILE.holdout == "hidden"
    assert HIDDEN_PROFILE.volatility_scale > PUBLIC_PROFILE.volatility_scale
    assert HIDDEN_PROFILE.depth_scale < PUBLIC_PROFILE.depth_scale
    public = _universe(profile=PUBLIC_PROFILE)
    hidden = _universe(profile=HIDDEN_PROFILE)
    assert public.holdout == "public"
    assert hidden.holdout == "hidden"
    assert public.market_logical_sha256 != hidden.market_logical_sha256


def test_a_universe_requires_at_least_one_security() -> None:
    with pytest.raises(ValueError):
        _universe(count=0)


def test_profile_rejects_negative_populations() -> None:
    with pytest.raises(ValueError):
        HoldoutProfile(
            label="bad",
            holdout="public",
            volatility_scale=1.0,
            depth_scale=1.0,
            maker_fee_bps=0,
            taker_fee_bps=0,
            momentum_crowding=1.0,
            market_makers=-1,
            fundamental_traders=0,
            momentum_traders=0,
            noise_traders=0,
        )
