"""Canonical UTC instant validation for kernel boundaries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def canonical_utc_instant(value: str) -> datetime:
    """Parse an exact ``YYYY-MM-DDTHH:MM:SSZ`` instant as an aware UTC datetime."""
    if not isinstance(value, str):
        raise TypeError("UTC instant must be a string")

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid UTC instant: {value!r}") from exc

    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"UTC instant must be timezone-aware: {value!r}")

    canonical = parsed.astimezone(UTC).isoformat(timespec="seconds")
    canonical = canonical.removesuffix("+00:00") + "Z"
    if value != canonical:
        raise ValueError(f"noncanonical UTC instant: {value!r}; expected {canonical!r}")
    return parsed
