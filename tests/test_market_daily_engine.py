"""Tests for the M10 deterministic daily market engine."""

from __future__ import annotations

import math
from datetime import date

import pytest

from app.market import engine
from app.market.calendar import easter_sunday, trading_days, xnys_holidays
from app.market.engine import (
    Bar,
    EntitySpec,
    GjrGarchT,
    MarketWorld,
    OhlcBoundError,
    brownian_bridge_bar,
    simulate_daily_market,
    standardized_t_draw,
    validate_bar,
)
from app.market.registry import market_registry
from app.world.rng import SemanticRNG

SESSION_START = date(2025, 1, 1)
SESSION_END = date(2025, 12, 31)


def _params(omega: float = 0.02, alpha: float = 0.05, gamma: float = 0.1, beta: float = 0.8) -> GjrGarchT:
    return GjrGarchT(omega=omega, alpha=alpha, gamma=gamma, beta=beta, nu=6.0)


def _world(session_count: int = 40, seed: int = 7, jumps: dict | None = None) -> MarketWorld:
    sessions = tuple(trading_days(date(2025, 1, 2), date(2025, 6, 30))[:session_count])
    return MarketWorld(
        world_id="m10-test",
        seed=seed,
        sessions=sessions,
        market=_params(),
        sectors={"Tech": _params(), "Energy": _params()},
        entities=(
            EntitySpec("AAA", "Tech", beta=1.1, sector_beta=0.5, drift=0.0002, garch=_params()),
            EntitySpec("BBB", "Energy", beta=0.9, sector_beta=0.4, drift=0.0001, garch=_params()),
        ),
        jumps=jumps or {},
    )


# --------------------------------------------------------------------------- calendar


def test_xnys_calendar_marks_the_published_holidays() -> None:
    holidays = xnys_holidays(2025)
    expected = {
        date(2025, 1, 1),  # New Year's Day
        date(2025, 1, 20),  # MLK
        date(2025, 2, 17),  # Presidents' Day
        date(2025, 4, 18),  # Good Friday
        date(2025, 5, 26),  # Memorial Day
        date(2025, 6, 19),  # Juneteenth
        date(2025, 7, 4),  # Independence Day
        date(2025, 9, 1),  # Labor Day
        date(2025, 11, 27),  # Thanksgiving
        date(2025, 12, 25),  # Christmas
    }
    assert holidays == expected


def test_xnys_calendar_observes_weekend_fixed_holidays() -> None:
    assert easter_sunday(2025) == date(2025, 4, 20)
    # 2021-01-01 was a Friday, so it is observed on the day itself.
    assert date(2021, 1, 1) in xnys_holidays(2021)
    # 2022-01-01 was a Saturday, observed on 2021-12-31.
    assert date(2021, 12, 31) in xnys_holidays(2021)
    # 2021-07-04 was a Sunday, observed on 2021-07-05.
    assert date(2021, 7, 5) in xnys_holidays(2021)


def test_trading_days_exclude_weekends_and_holidays() -> None:
    sessions = trading_days(SESSION_START, SESSION_END)
    assert sessions == sorted(sessions)
    assert len(sessions) == len(set(sessions))
    assert all(day.weekday() < 5 for day in sessions)
    holidays = xnys_holidays(2025)
    assert not (set(sessions) & holidays)
    assert date(2025, 1, 2) in sessions
    assert date(2025, 7, 4) not in sessions
    assert 249 <= len(sessions) <= 252


# --------------------------------------------------------------------------- semantic RNG


def test_semantic_draws_reproduce_and_seed_changes_them() -> None:
    first = SemanticRNG("world-a", 11).stream("ENTITY", "mech").normal("x", 3, 0)
    second = SemanticRNG("world-a", 11).stream("ENTITY", "mech").normal("x", 3, 0)
    other_seed = SemanticRNG("world-a", 12).stream("ENTITY", "mech").normal("x", 3, 0)
    assert first == second
    assert first != other_seed


def test_unrelated_mechanism_does_not_shift_a_stream() -> None:
    rng = SemanticRNG("world-a", 11)
    stream = rng.stream("ENTITY", "mech")
    baseline = stream.normal("x", 5, 0)
    # Creating and drawing from an unrelated mechanism must not disturb "mech".
    other = rng.stream("ENTITY", "other-mech")
    other.normal("y", 5, 0)
    rng.stream("DIFFERENT-ENTITY", "mech").normal("x", 5, 0)
    assert stream.normal("x", 5, 0) == baseline
    assert stream.normal("x", 5, 0) != other.normal("y", 5, 0)


# --------------------------------------------------------------------------- GJR-GARCH-t


def test_gjr_garch_t_recursion_matches_closed_form(monkeypatch: pytest.MonkeyPatch) -> None:
    shocks = [1.0, -2.0, 0.5, 1.5]
    monkeypatch.setattr(engine, "standardized_t_draw", lambda _s, _v, ordinal, _nu: shocks[ordinal])
    params = GjrGarchT(omega=0.02, alpha=0.05, gamma=0.1, beta=0.8, nu=6.0)
    sessions = [date(2025, 1, day) for day in range(2, 6)]
    stream = SemanticRNG("w", 1).stream("E", "m")
    path = engine._gjr_garch_t_path(stream, params, sessions, "shock")

    unconditional = 0.02 / (1 - (0.05 + 0.05 + 0.8))
    sigma2 = [unconditional]
    eps = [math.sqrt(unconditional) * 1.0]
    for index in range(1, len(shocks)):
        previous = eps[index - 1]
        previous_sigma2 = sigma2[index - 1]
        asymmetry = previous * previous if previous < 0 else 0.0
        variance = 0.02 + 0.05 * previous * previous + 0.1 * asymmetry + 0.8 * previous_sigma2
        sigma2.append(variance)
        eps.append(math.sqrt(variance) * shocks[index])
    assert path == pytest.approx(eps)


def test_standardized_t_scales_by_the_chi_square(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = SemanticRNG("w", 42).stream("E", "m")
    z = stream.normal("shock.z", 0, 0)
    monkeypatch.setattr(engine, "_chi_square_draw", lambda *_args, **_kwargs: 8.0)
    value = standardized_t_draw(stream, "shock", 0, nu=5.0)
    assert value == pytest.approx(z * math.sqrt(3.0 / 8.0))


def test_standardized_t_has_unit_variance_scale() -> None:
    # The draw is the *unit-variance* Student-t, so its variance is 1 (the raw
    # Student-t variance nu/(nu-2) is removed by the sqrt((nu-2)/chi2) scaling).
    stream = SemanticRNG("w", 5).stream("E", "t")
    draws = [standardized_t_draw(stream, "shock", index, nu=5.0) for index in range(4000)]
    mean = sum(draws) / len(draws)
    variance = sum((value - mean) ** 2 for value in draws) / (len(draws) - 1)
    assert abs(mean) < 0.15
    assert 0.85 < variance < 1.15


# --------------------------------------------------------------------------- OHLC bounds


def test_brownian_bridge_bars_respect_bounds() -> None:
    stream = SemanticRNG("w", 3).stream("C", "market.ohlc")
    previous = 100.0
    for ordinal in range(200):
        close = previous * math.exp(0.01)
        bar = brownian_bridge_bar(
            symbol="AAA",
            session=date(2025, 1, 2),
            previous_close=previous,
            close=close,
            stream=stream,
            ordinal=ordinal,
            sigma_open=0.004,
            sigma_range=0.006,
        )
        validate_bar(bar)
        assert bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high
        previous = close


@pytest.mark.parametrize(
    "bar",
    [
        Bar("AAA", date(2025, 1, 2), open=10.0, high=11.0, low=10.5, close=10.0),
        Bar("AAA", date(2025, 1, 2), open=10.0, high=10.2, low=9.0, close=10.5),
        Bar("AAA", date(2025, 1, 2), open=10.0, high=9.0, low=9.5, close=10.0),
        Bar("AAA", date(2025, 1, 2), open=0.0, high=11.0, low=9.0, close=10.0),
        Bar("AAA", date(2025, 1, 2), open=10.0, high=11.0, low=9.0, close=float("nan")),
    ],
)
def test_planted_invalid_bars_are_rejected(bar: Bar) -> None:
    with pytest.raises(OhlcBoundError):
        validate_bar(bar)


# --------------------------------------------------------------------------- end to end


def test_world_is_deterministic_and_replica_hash_stable() -> None:
    first = simulate_daily_market(_world())
    second = simulate_daily_market(_world())
    assert first.logical_sha256 == second.logical_sha256
    assert first.series["AAA"].closes == second.series["AAA"].closes
    for series in first.series.values():
        for bar in series.bars:
            validate_bar(bar)


def test_a_different_seed_produces_a_different_world() -> None:
    assert simulate_daily_market(_world(seed=7)).logical_sha256 != (
        simulate_daily_market(_world(seed=8)).logical_sha256
    )


def test_event_jump_twin_only_changes_descendants() -> None:
    baseline = simulate_daily_market(_world())
    jumped = simulate_daily_market(_world(jumps={"AAA": {date(2025, 1, 6): 0.05}}))
    assert jumped.logical_sha256 != baseline.logical_sha256
    # AAA is the intervened company: its close changes.
    assert jumped.series["AAA"].closes != baseline.series["AAA"].closes
    # BBB is a non-descendant: every bar is byte-identical.
    assert jumped.series["BBB"].bars == baseline.series["BBB"].bars
    # The world and sector factors are upstream of the jump and must not move.
    assert jumped.market_factor == baseline.market_factor
    assert jumped.sector_factors["Energy"] == baseline.sector_factors["Energy"]


def test_registry_is_acyclic_and_descendants_are_company_scoped() -> None:
    registry = market_registry()
    assert isinstance(registry.period_dag(), tuple)
    targets = registry.descendants(
        {("event_jump", "COMPANY:AAA")},
        sector_of_company={"AAA": "Tech", "BBB": "Energy"},
        sectors={"Tech", "Energy"},
    )
    assert ("log_return", "COMPANY:AAA") in targets
    assert ("close", "COMPANY:AAA") in targets
    assert ("open", "COMPANY:AAA") in targets
    assert all(entity != "COMPANY:BBB" for _variable, entity in targets)
