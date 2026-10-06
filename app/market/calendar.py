"""Rule-based XNYS trading calendar (Milestone 10).

The daily market engine needs exchange sessions, not weekday placeholders.
``app/market_data/calendar.py`` already forbids presenting a weekday fixture as
an actual exchange calendar; this module supplies the named-exchange calendar it
refers to. It is pure and rule-based (no dependency on a market-calendar
package), so the same date range always yields the same sessions.

Observed-day rules follow the NYSE convention: a fixed-date holiday that falls
on a Saturday is observed the preceding Friday, and one that falls on a Sunday
is observed the following Monday. Floating holidays (Martin Luther King Jr.
Day, Presidents' Day, Memorial Day, Labor Day, Thanksgiving) are defined by
weekday-of-month; Good Friday is the Friday before Easter Sunday.
"""

from __future__ import annotations

from datetime import date, timedelta

__all__ = ["XNYS", "MarketCalendar", "easter_sunday", "xnys_holidays", "trading_days"]


def easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (anonymous computus), used for Good Friday."""

    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    """``occurrence``-th ``weekday`` (0=Mon) of a month; 1-based."""

    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def _observed(day: date) -> date:
    if day.weekday() == 5:  # Saturday -> preceding Friday
        return day - timedelta(days=1)
    if day.weekday() == 6:  # Sunday -> following Monday
        return day + timedelta(days=1)
    return day


def _nominal_holidays(year: int) -> set[date]:
    """Observed dates of the market holidays whose nominal date is in ``year``."""

    return {
        _observed(date(year, 1, 1)),  # New Year's Day
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday / Presidents' Day
        easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed(date(year, 6, 19)),  # Juneteenth National Independence Day
        _observed(date(year, 7, 4)),  # Independence Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving Day
        _observed(date(year, 12, 25)),  # Christmas Day
    }


def xnys_holidays(year: int) -> frozenset[date]:
    """Observed XNYS market holidays falling in calendar ``year``.

    A fixed-date holiday observed on the preceding Friday can land in the prior
    year (e.g. New Year's Day 2022, a Saturday, closes the market on
    2021-12-31), so the neighbouring nominal years are folded in and filtered to
    the requested year.
    """

    holidays: set[date] = set()
    for nominal in (year - 1, year, year + 1):
        holidays |= _nominal_holidays(nominal)
    return frozenset(day for day in holidays if day.year == year)


def trading_days(start: date, end: date) -> list[date]:
    """XNYS sessions in ``[start, end]`` (inclusive), in ascending order."""

    if end < start:
        raise ValueError("end must not precede start")
    years = range(start.year, end.year + 1)
    holidays: set[date] = set()
    for year in years:
        holidays |= xnys_holidays(year)
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5 and current not in holidays:
            days.append(current)
        current += timedelta(days=1)
    return days


class MarketCalendar:
    """A named-exchange session calendar. Only ``XNYS`` is defined today."""

    __slots__ = ("exchange",)

    def __init__(self, exchange: str = "XNYS") -> None:
        if exchange != "XNYS":
            raise ValueError(f"unknown exchange calendar: {exchange!r}")
        self.exchange = exchange

    def is_session(self, day: date) -> bool:
        return day.weekday() < 5 and day not in xnys_holidays(day.year)

    def sessions(self, start: date, end: date) -> list[date]:
        return trading_days(start, end)


XNYS = MarketCalendar("XNYS")
