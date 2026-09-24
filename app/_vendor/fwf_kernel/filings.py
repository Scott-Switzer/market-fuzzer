"""Immutable filing versions, correction classification, and public PIT history.

The filing layer records publication and reliance metadata only.  It never derives,
mutates, or stores financial statement values: a version points at an independently
produced logical payload by SHA-256.  The accounting ledger and equity module remain the
authorities for monetary facts.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Final, cast

from .temporal import canonical_utc_instant


class FilingKind(StrEnum):
    TEN_Q = "10-Q"
    TEN_K = "10-K"


class CorrectionClass(StrEnum):
    BIG_R = "BIG_R"
    LITTLE_R = "LITTLE_R"


class FilingRelianceStatus(StrEnum):
    RELIABLE_AS_FILED = "RELIABLE_AS_FILED"
    NON_RELIANCE = "NON_RELIANCE"
    SUPERSEDED = "SUPERSEDED"
    CURRENT_RESTATED = "CURRENT_RESTATED"
    CURRENT_REVISED = "CURRENT_REVISED"


PIT_FILING_VERSION_RULE: Final = "PIT-FILING-VERSION"
PIT_NON_RELIANCE_RULE: Final = "PIT-NON-RELIANCE"
PIT_FILING_ASOF_RULE: Final = "PIT-FILING-ASOF"
ACCT_RESTATEMENT_CLASS_RULE: Final = "ACCT-RESTATEMENT-CLASS"

FILING_RULES: Final[dict[str, str]] = {
    PIT_FILING_VERSION_RULE: "filing versions form an append-only linear chain with canonical availability",
    PIT_NON_RELIANCE_RULE: "public non-reliance begins only at non_reliance_available_at",
    PIT_FILING_ASOF_RULE: "future versions and notices cannot alter historical as-of results",
    ACCT_RESTATEMENT_CLASS_RULE: "BIG_R and LITTLE_R have distinct structural requirements",
}
# These aliases are intentionally the same four-rule registry, not additional rules.
RECONCILIATION_RULES = FILING_RULES
DIRECT_RULES = FILING_RULES

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _integer(value: int, field_name: str, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field_name} must be an integer >= {minimum}")


def _calendar_date(value: date, field_name: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{field_name} must be a date")
    return value


def _instant(value: str, field_name: str) -> datetime:
    return canonical_utc_instant(value)


def _sha256(value: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError("payload_sha256 must be an exact lowercase 64-character SHA-256")
    return value


def _enum[T: StrEnum](value: T | str, enum_type: type[T], field_name: str) -> T:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} has an invalid value: {value!r}") from exc


@dataclass(frozen=True, slots=True)
class FilingVersion:
    """One immutable public version of a filing series.

    ``payload_sha256`` is a commitment to content derived elsewhere.  This class has no
    statement dictionary, amount, or accounting mutation API by design.
    """

    filing_id: str
    version_id: str
    entity_id: str
    form: FilingKind
    period: int
    period_end: date
    version: int
    payload_sha256: str
    available_at: str
    supersedes_version_id: str | None = None
    correction_class: CorrectionClass | None = None

    def __post_init__(self) -> None:
        _text(self.filing_id, "filing_id")
        _text(self.version_id, "version_id")
        _text(self.entity_id, "entity_id")
        object.__setattr__(self, "form", _enum(self.form, FilingKind, "form"))
        _integer(self.period, "period", 1)
        object.__setattr__(self, "period_end", _calendar_date(self.period_end, "period_end"))
        _integer(self.version, "version", 1)
        object.__setattr__(self, "payload_sha256", _sha256(self.payload_sha256))
        _instant(self.available_at, "available_at")
        if self.supersedes_version_id is not None:
            _text(self.supersedes_version_id, "supersedes_version_id")
            if self.supersedes_version_id == self.version_id:
                raise ValueError("a filing version cannot supersede itself")
        if self.correction_class is not None:
            object.__setattr__(
                self,
                "correction_class",
                _enum(self.correction_class, CorrectionClass, "correction_class"),
            )
        if self.version == 1:
            if self.supersedes_version_id is not None or self.correction_class is not None:
                raise ValueError("an original filing version cannot claim a correction")
        elif self.supersedes_version_id is None or self.correction_class is None:
            raise ValueError("a corrected filing version requires supersession and classification")

    @property
    def id(self) -> str:
        return self.version_id

    @property
    def is_original(self) -> bool:
        return self.version == 1


@dataclass(frozen=True, slots=True)
class NonRelianceNotice:
    """Public non-reliance metadata; ``conclusion_at`` is never a visibility clock."""

    notice_id: str
    entity_id: str
    affected_version_ids: tuple[str, ...]
    conclusion_at: str
    available_at: str
    reason: str

    def __post_init__(self) -> None:
        _text(self.notice_id, "notice_id")
        _text(self.entity_id, "entity_id")
        if isinstance(self.affected_version_ids, str):
            raise TypeError("affected_version_ids must be a sequence of version IDs")
        try:
            affected = tuple(self.affected_version_ids)
        except TypeError as exc:
            raise TypeError("affected_version_ids must be a sequence of version IDs") from exc
        if not affected:
            raise ValueError("affected_version_ids must not be empty")
        for version_id in affected:
            _text(version_id, "affected_version_id")
        if len(set(affected)) != len(affected):
            raise ValueError("affected_version_ids must not contain duplicates")
        object.__setattr__(self, "affected_version_ids", affected)
        conclusion = _instant(self.conclusion_at, "conclusion_at")
        available = _instant(self.available_at, "available_at")
        if conclusion > available:
            raise ValueError("conclusion_at must be <= available_at")
        _text(self.reason, "reason")

    @property
    def id(self) -> str:
        return self.notice_id


class FilingBook:
    """Append-only filing metadata with public point-in-time queries.

    All public collection views are tuples.  A candidate record is completely validated
    before either private collection is changed, so a rejected operation is atomic.
    """

    def __init__(self) -> None:
        self._versions: dict[str, FilingVersion] = {}
        self._by_filing: dict[str, list[FilingVersion]] = {}
        self._notices: dict[str, NonRelianceNotice] = {}
        self._notices_by_version: dict[str, list[NonRelianceNotice]] = {}

    @property
    def versions(self) -> tuple[FilingVersion, ...]:
        return tuple(self._versions.values())

    @property
    def non_reliance_notices(self) -> tuple[NonRelianceNotice, ...]:
        return tuple(self._notices.values())

    def get_version(self, version_id: str) -> FilingVersion | None:
        return self._versions.get(version_id)

    def notices_for_version(self, version_id: str) -> tuple[NonRelianceNotice, ...]:
        return tuple(self._notices_by_version.get(version_id, ()))

    @staticmethod
    def _coerce_version(
        record: FilingVersion | str,
        version_id: str | None,
        entity_id: str | None,
        form: FilingKind | str | None,
        period: int | None,
        period_end: date | None,
        version: int | None,
        payload_sha256: str | None,
        available_at: str | None,
        supersedes_version_id: str | None,
        correction_class: CorrectionClass | str | None,
    ) -> FilingVersion:
        if isinstance(record, FilingVersion):
            if any(
                value is not None
                for value in (
                    version_id,
                    entity_id,
                    form,
                    period,
                    period_end,
                    version,
                    payload_sha256,
                    available_at,
                    supersedes_version_id,
                    correction_class,
                )
            ):
                raise TypeError("a FilingVersion object cannot be combined with field arguments")
            return record
        required = (
            version_id,
            entity_id,
            form,
            period,
            period_end,
            version,
            payload_sha256,
            available_at,
        )
        if any(value is None for value in required):
            raise TypeError("all filing version fields are required")
        assert version_id is not None
        assert entity_id is not None
        assert form is not None
        assert period is not None
        assert period_end is not None
        assert version is not None
        assert payload_sha256 is not None
        assert available_at is not None
        return FilingVersion(
            filing_id=record,
            version_id=version_id,
            entity_id=entity_id,
            form=cast(FilingKind, form),
            period=period,
            period_end=period_end,
            version=version,
            payload_sha256=payload_sha256,
            available_at=available_at,
            supersedes_version_id=supersedes_version_id,
            correction_class=cast(CorrectionClass | None, correction_class),
        )

    def _validate_version_append(self, candidate: FilingVersion) -> None:
        if candidate.version_id in self._versions:
            raise ValueError(f"duplicate filing version_id: {candidate.version_id}")
        series = self._by_filing.get(candidate.filing_id, [])
        if candidate.version == 1:
            if series:
                raise ValueError("a filing series may have only one original version")
            return
        if not series:
            raise ValueError("a corrected version requires an existing filing series")
        prior_id = candidate.supersedes_version_id
        if prior_id is None:
            raise ValueError("a corrected version requires supersedes_version_id")
        prior = self._versions.get(prior_id)
        if prior is None:
            raise ValueError("supersedes_version_id does not identify an existing version")
        if prior.filing_id != candidate.filing_id:
            raise ValueError("a version cannot supersede a different filing series")
        if prior.version != candidate.version - 1:
            raise ValueError("filing version numbers must be contiguous")
        if any(version.supersedes_version_id == prior.version_id for version in series):
            raise ValueError("a filing series cannot branch from a superseded version")
        if _instant(candidate.available_at, "available_at") <= _instant(
            prior.available_at, "available_at"
        ):
            raise ValueError("a corrected version must be available strictly after its predecessor")
        if (
            candidate.entity_id != prior.entity_id
            or candidate.form is not prior.form
            or candidate.period != prior.period
            or candidate.period_end != prior.period_end
        ):
            raise ValueError(
                "entity, form, period, and period_end cannot change in a filing series"
            )
        if candidate.correction_class is CorrectionClass.BIG_R and not self._has_big_r_notice(
            prior, candidate
        ):
            raise ValueError("a BIG_R correction requires a public non-reliance notice")

    def _append_version(self, candidate: FilingVersion) -> FilingVersion:
        self._validate_version_append(candidate)
        self._versions[candidate.version_id] = candidate
        self._by_filing.setdefault(candidate.filing_id, []).append(candidate)
        return candidate

    def _has_big_r_notice(self, prior: FilingVersion, candidate: FilingVersion) -> bool:
        candidate_available = _instant(candidate.available_at, "available_at")
        return any(
            notice.entity_id == prior.entity_id
            and prior.version_id in notice.affected_version_ids
            and _instant(notice.available_at, "available_at") <= candidate_available
            for notice in self._notices.values()
        )

    def publish_original(
        self,
        filing_id: str | FilingVersion,
        version_id: str | None = None,
        entity_id: str | None = None,
        form: FilingKind | str | None = None,
        period: int | None = None,
        period_end: date | None = None,
        payload_sha256: str | None = None,
        available_at: str | None = None,
    ) -> FilingVersion:
        candidate = self._coerce_version(
            filing_id,
            version_id,
            entity_id,
            form,
            period,
            period_end,
            1,
            payload_sha256,
            available_at,
            None,
            None,
        )
        if candidate.version != 1:
            raise ValueError("publish_original requires version 1")
        return self._append_version(candidate)

    def publish_correction(
        self,
        filing_id: str | FilingVersion,
        version_id: str | None = None,
        entity_id: str | None = None,
        form: FilingKind | str | None = None,
        period: int | None = None,
        period_end: date | None = None,
        version: int | None = None,
        payload_sha256: str | None = None,
        available_at: str | None = None,
        supersedes_version_id: str | None = None,
        correction_class: CorrectionClass | str | None = None,
    ) -> FilingVersion:
        candidate = self._coerce_version(
            filing_id,
            version_id,
            entity_id,
            form,
            period,
            period_end,
            version,
            payload_sha256,
            available_at,
            supersedes_version_id,
            correction_class,
        )
        if candidate.version == 1:
            raise ValueError("publish_correction requires version > 1")
        return self._append_version(candidate)

    def append_version(self, version: FilingVersion) -> FilingVersion:
        """Controlled generic append operation for callers constructing a record first."""
        if not isinstance(version, FilingVersion):
            raise TypeError("expected FilingVersion")
        return self._append_version(version)

    def add_version(self, version: FilingVersion) -> FilingVersion:
        """Alias for :meth:`append_version` for boundary adapters."""
        return self.append_version(version)

    def publish(self, version: FilingVersion) -> FilingVersion:
        """Alias for :meth:`append_version` for boundary adapters."""
        return self.append_version(version)

    def _validate_notice_append(self, candidate: NonRelianceNotice) -> None:
        if candidate.notice_id in self._notices:
            raise ValueError(f"duplicate non-reliance notice_id: {candidate.notice_id}")
        for version_id in candidate.affected_version_ids:
            version = self._versions.get(version_id)
            if version is None:
                raise ValueError("non-reliance notice references an unknown version")
            if version.entity_id != candidate.entity_id:
                raise ValueError("non-reliance notice affects another entity")
            if _instant(candidate.available_at, "available_at") <= _instant(
                version.available_at, "available_at"
            ):
                raise ValueError("non-reliance notice must be available after each affected filing")

    def declare_non_reliance(
        self,
        notice_id: str | NonRelianceNotice,
        entity_id: str | None = None,
        affected_version_ids: Iterable[str] | None = None,
        conclusion_at: str | None = None,
        available_at: str | None = None,
        reason: str | None = None,
    ) -> NonRelianceNotice:
        if isinstance(notice_id, NonRelianceNotice):
            if any(
                value is not None
                for value in (
                    entity_id,
                    affected_version_ids,
                    conclusion_at,
                    available_at,
                    reason,
                )
            ):
                raise TypeError(
                    "a NonRelianceNotice object cannot be combined with field arguments"
                )
            candidate = notice_id
        else:
            if any(
                value is None
                for value in (entity_id, affected_version_ids, conclusion_at, available_at, reason)
            ):
                raise TypeError("all non-reliance notice fields are required")
            assert entity_id is not None
            assert affected_version_ids is not None
            assert conclusion_at is not None
            assert available_at is not None
            assert reason is not None
            candidate = NonRelianceNotice(
                notice_id=notice_id,
                entity_id=entity_id,
                affected_version_ids=tuple(affected_version_ids),
                conclusion_at=conclusion_at,
                available_at=available_at,
                reason=reason,
            )
        self._validate_notice_append(candidate)
        self._notices[candidate.notice_id] = candidate
        for version_id in candidate.affected_version_ids:
            self._notices_by_version.setdefault(version_id, []).append(candidate)
        return candidate

    def add_non_reliance_notice(self, notice: NonRelianceNotice) -> NonRelianceNotice:
        if not isinstance(notice, NonRelianceNotice):
            raise TypeError("expected NonRelianceNotice")
        return self.declare_non_reliance(notice)

    @staticmethod
    def _as_of(value: str) -> datetime:
        return _instant(value, "as_of")

    def current_version(self, filing_id: str, as_of: str) -> FilingVersion | None:
        _text(filing_id, "filing_id")
        cutoff = self._as_of(as_of)
        visible = [
            version
            for version in self._by_filing.get(filing_id, ())
            if _instant(version.available_at, "available_at") <= cutoff
        ]
        return max(visible, key=lambda version: version.version, default=None)

    def version_status(self, version_id: str, as_of: str) -> FilingRelianceStatus | None:
        _text(version_id, "version_id")
        cutoff = self._as_of(as_of)
        version = self._versions.get(version_id)
        if version is None or _instant(version.available_at, "available_at") > cutoff:
            return None
        public_notice = any(
            _instant(notice.available_at, "available_at") <= cutoff
            for notice in self._notices_by_version.get(version_id, ())
        )
        if public_notice:
            return FilingRelianceStatus.NON_RELIANCE
        successor = next(
            (
                candidate
                for candidate in self._by_filing.get(version.filing_id, ())
                if candidate.supersedes_version_id == version_id
                and _instant(candidate.available_at, "available_at") <= cutoff
            ),
            None,
        )
        if successor is not None:
            return FilingRelianceStatus.SUPERSEDED
        if version.correction_class is CorrectionClass.BIG_R:
            return FilingRelianceStatus.CURRENT_RESTATED
        if version.correction_class is CorrectionClass.LITTLE_R:
            return FilingRelianceStatus.CURRENT_REVISED
        return FilingRelianceStatus.RELIABLE_AS_FILED

    def history(self, filing_id: str, as_of: str | None = None) -> tuple[FilingVersion, ...]:
        _text(filing_id, "filing_id")
        series = tuple(self._by_filing.get(filing_id, ()))
        if as_of is None:
            return series
        cutoff = self._as_of(as_of)
        return tuple(
            version
            for version in series
            if _instant(version.available_at, "available_at") <= cutoff
        )

    def validate_reconciliations(self) -> set[str]:
        """Return broken direct M4.2B rules after defensive state inspection."""
        failures: set[str] = set()
        seen_ids: set[str] = set()
        for key, version in self._versions.items():
            try:
                if key != version.version_id or version.version_id in seen_ids:
                    raise ValueError("duplicate or mismatched version ID")
                seen_ids.add(version.version_id)
                if version.version == 1 and (
                    version.supersedes_version_id is not None
                    or version.correction_class is not None
                ):
                    raise ValueError("original classification mismatch")
                if version.version > 1 and (
                    version.supersedes_version_id is None or version.correction_class is None
                ):
                    raise ValueError("correction classification mismatch")
            except (AttributeError, TypeError, ValueError):
                failures.add(ACCT_RESTATEMENT_CLASS_RULE)

        for filing_id, series in self._by_filing.items():
            try:
                if not series or series[0].version != 1:
                    raise ValueError("series lacks an original")
                first = series[0]
                for index, version in enumerate(series):
                    if version.filing_id != filing_id:
                        raise ValueError("cross-series record")
                    if (
                        version.entity_id != first.entity_id
                        or version.form is not first.form
                        or version.period != first.period
                        or version.period_end != first.period_end
                    ):
                        raise ValueError("series metadata changed")
                    if index == 0:
                        if version.version != 1 or version.supersedes_version_id is not None:
                            raise ValueError("invalid original")
                    else:
                        prior = series[index - 1]
                        if version.version != prior.version + 1:
                            raise ValueError("version gap")
                        if version.supersedes_version_id != prior.version_id:
                            raise ValueError("branch or cycle")
                        if _instant(version.available_at, "available_at") <= _instant(
                            prior.available_at, "available_at"
                        ):
                            raise ValueError("availability moved backwards")
            except (AttributeError, TypeError, ValueError):
                failures.add(PIT_FILING_VERSION_RULE)

        for notice in self._notices.values():
            try:
                if not notice.affected_version_ids or len(set(notice.affected_version_ids)) != len(
                    notice.affected_version_ids
                ):
                    raise ValueError("invalid affected list")
                for version_id in notice.affected_version_ids:
                    affected_version = self._versions.get(version_id)
                    if affected_version is None or affected_version.entity_id != notice.entity_id:
                        raise ValueError("invalid affected version")
                    if _instant(notice.available_at, "available_at") <= _instant(
                        affected_version.available_at, "available_at"
                    ):
                        raise ValueError("notice is not publicly later")
            except (AttributeError, TypeError, ValueError):
                failures.add(PIT_NON_RELIANCE_RULE)

        for series in self._by_filing.values():
            for version in series:
                if version.correction_class is not CorrectionClass.BIG_R:
                    continue
                prior_id = version.supersedes_version_id
                if prior_id is None or not any(
                    prior_id in notice.affected_version_ids
                    and notice.entity_id == version.entity_id
                    and _instant(notice.available_at, "available_at")
                    <= _instant(version.available_at, "available_at")
                    for notice in self._notices.values()
                ):
                    failures.add(ACCT_RESTATEMENT_CLASS_RULE)

        # Check the defining anti-lookahead property against every public cutoff.
        for filing_id, series in self._by_filing.items():
            for version in series:
                cutoff = version.available_at
                if self.current_version(filing_id, cutoff) is None:
                    failures.add(PIT_FILING_ASOF_RULE)
        return failures

    def validate_invariants(self) -> set[str]:
        return self.validate_reconciliations()


# The requested terminology is FilingBook; FilingHistory is a readable compatibility name.
FilingHistory = FilingBook
