from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal

import pytest

from fwf_kernel import equity as E
from fwf_kernel import ledger as L
from fwf_kernel.filings import (
    FILING_RULES,
    CorrectionClass,
    FilingBook,
    FilingKind,
    FilingRelianceStatus,
    FilingVersion,
    NonRelianceNotice,
)

ENTITY = "ENT-FILINGS"
FILING = "FILING-001"
PERIOD_END = date(2025, 3, 31)
T0 = "2025-04-01T00:00:00Z"
T1 = "2025-04-02T00:00:00Z"
T2 = "2025-04-03T00:00:00Z"
T3 = "2025-04-04T00:00:00Z"
T4 = "2025-04-05T00:00:00Z"
T5 = "2025-04-06T00:00:00Z"
HASH0 = "0" * 64
HASH1 = "1" * 64
HASH2 = "2" * 64
HASH3 = "3" * 64


def original_book() -> tuple[FilingBook, FilingVersion]:
    book = FilingBook()
    version = book.publish_original(
        FILING,
        "v1",
        ENTITY,
        FilingKind.TEN_Q,
        1,
        PERIOD_END,
        HASH0,
        T0,
    )
    return book, version


def notice(
    book: FilingBook,
    version_id: str = "v1",
    *,
    notice_id: str = "n1",
    conclusion_at: str = T1,
    available_at: str = T2,
) -> NonRelianceNotice:
    return book.declare_non_reliance(
        notice_id, ENTITY, [version_id], conclusion_at, available_at, "error"
    )


def correction(
    book: FilingBook,
    version_id: str,
    version: int,
    prior_id: str,
    available_at: str,
    correction_class: CorrectionClass,
    payload_sha256: str,
) -> FilingVersion:
    return book.publish_correction(
        FILING,
        version_id,
        ENTITY,
        FilingKind.TEN_Q,
        1,
        PERIOD_END,
        version,
        payload_sha256,
        available_at,
        prior_id,
        correction_class,
    )


def test_original_is_not_visible_before_and_visible_exactly_at_availability() -> None:
    book, version = original_book()
    assert book.current_version(FILING, "2025-03-31T23:59:59Z") is None
    assert book.version_status(version.version_id, "2025-03-31T23:59:59Z") is None
    assert book.current_version(FILING, T0) == version
    assert book.version_status(version.version_id, T0) is FilingRelianceStatus.RELIABLE_AS_FILED


def test_internal_conclusion_is_not_public_until_notice_availability() -> None:
    book, version = original_book()
    notice(book)
    assert book.current_version(FILING, T1) == version
    assert book.version_status(version.version_id, T1) is FilingRelianceStatus.RELIABLE_AS_FILED
    assert book.current_version(FILING, T2) == version
    assert book.version_status(version.version_id, T2) is FilingRelianceStatus.NON_RELIANCE


def test_big_r_correction_is_unavailable_until_its_own_public_time() -> None:
    book, original = original_book()
    notice(book)
    corrected = correction(book, "v2", 2, "v1", T3, CorrectionClass.BIG_R, HASH1)
    assert book.current_version(FILING, "2025-04-03T23:59:59Z") == original
    assert book.current_version(FILING, T3) == corrected
    assert book.version_status("v1", T3) is FilingRelianceStatus.NON_RELIANCE
    assert book.version_status("v2", T3) is FilingRelianceStatus.CURRENT_RESTATED


def test_original_remains_queryable_and_pre_notice_history_is_unchanged() -> None:
    book, original = original_book()
    before = book.current_version(FILING, T1)
    before_status = book.version_status(original.version_id, T1)
    notice(book)
    correction(book, "v2", 2, "v1", T3, CorrectionClass.BIG_R, HASH1)
    assert book.current_version(FILING, T1) == before
    assert book.version_status(original.version_id, T1) == before_status
    assert book.get_version("v1") == original
    assert book.history(FILING, T1) == (original,)
    assert book.history(FILING, T3) == (original, book.get_version("v2"))


def test_little_r_requires_no_notice_and_marks_old_version_superseded() -> None:
    book, original = original_book()
    revised = correction(book, "v2", 2, "v1", T3, CorrectionClass.LITTLE_R, HASH1)
    assert book.current_version(FILING, T2) == original
    assert book.version_status("v1", T2) is FilingRelianceStatus.RELIABLE_AS_FILED
    assert book.current_version(FILING, T3) == revised
    assert book.version_status("v1", T3) is FilingRelianceStatus.SUPERSEDED
    assert book.version_status("v2", T3) is FilingRelianceStatus.CURRENT_REVISED
    assert book.non_reliance_notices == ()


def test_big_r_without_public_notice_is_rejected_atomically() -> None:
    book, _ = original_book()
    before = (book.versions, book.non_reliance_notices)
    with pytest.raises(ValueError, match="non-reliance notice"):
        correction(book, "v2", 2, "v1", T3, CorrectionClass.BIG_R, HASH1)
    assert (book.versions, book.non_reliance_notices) == before


def test_notice_ordering_and_affected_version_validation_are_atomic() -> None:
    book, _ = original_book()
    before = (book.versions, book.non_reliance_notices)
    with pytest.raises(ValueError):
        book.declare_non_reliance("bad-order", ENTITY, ["v1"], T2, T1, "reason")
    with pytest.raises(ValueError):
        book.declare_non_reliance("too-early", ENTITY, ["v1"], T0, T0, "reason")
    with pytest.raises(ValueError):
        book.declare_non_reliance("missing", ENTITY, ["missing"], T1, T2, "reason")
    with pytest.raises(ValueError):
        book.declare_non_reliance("duplicate", ENTITY, ["v1", "v1"], T1, T2, "reason")
    assert (book.versions, book.non_reliance_notices) == before


def test_notice_and_correction_may_be_public_simultaneously() -> None:
    book, _ = original_book()
    book.declare_non_reliance("n1", ENTITY, ["v1"], T1, T3, "error")
    corrected = correction(book, "v2", 2, "v1", T3, CorrectionClass.BIG_R, HASH1)
    assert book.current_version(FILING, "2025-04-02T23:59:59Z").version_id == "v1"
    assert book.current_version(FILING, T3) == corrected


def test_version_chain_is_linear_and_supports_three_revisions() -> None:
    book, _ = original_book()
    notice(book, "v1", notice_id="n1", available_at=T2)
    v2 = correction(book, "v2", 2, "v1", T3, CorrectionClass.BIG_R, HASH1)
    notice(book, "v2", notice_id="n2", conclusion_at=T3, available_at=T4)
    v3 = correction(book, "v3", 3, "v2", T5, CorrectionClass.LITTLE_R, HASH2)
    assert book.current_version(FILING, T4) == v2
    assert book.current_version(FILING, T5) == v3
    assert book.version_status("v1", T5) is FilingRelianceStatus.NON_RELIANCE
    assert book.version_status("v2", T5) is FilingRelianceStatus.NON_RELIANCE
    assert book.version_status("v3", T5) is FilingRelianceStatus.CURRENT_REVISED
    assert book.history(FILING) == (book.get_version("v1"), v2, v3)


def test_duplicate_gap_branch_self_and_cross_series_supersession_are_rejected() -> None:
    book, _ = original_book()
    notice(book)
    v2 = correction(book, "v2", 2, "v1", T3, CorrectionClass.LITTLE_R, HASH1)
    with pytest.raises(ValueError, match="duplicate"):
        correction(book, "v2", 2, "v1", T3, CorrectionClass.LITTLE_R, HASH1)
    with pytest.raises(ValueError, match="contiguous"):
        correction(book, "v4", 4, "v2", T4, CorrectionClass.LITTLE_R, HASH2)
    with pytest.raises(ValueError, match="branch"):
        correction(book, "branch", 2, "v1", T4, CorrectionClass.LITTLE_R, HASH2)
    with pytest.raises(ValueError, match="supersede itself"):
        FilingVersion(
            FILING,
            "self",
            ENTITY,
            FilingKind.TEN_Q,
            1,
            PERIOD_END,
            2,
            HASH2,
            T4,
            "self",
            CorrectionClass.LITTLE_R,
        )
    with pytest.raises(ValueError, match="different filing"):
        other = book.publish_original(
            "FILING-002", "other-v1", ENTITY, FilingKind.TEN_K, 1, PERIOD_END, HASH2, T0
        )
        correction(book, "v3", 3, other.version_id, T4, CorrectionClass.LITTLE_R, HASH2)
    assert book.history(FILING)[-1] == v2


def test_availability_and_series_metadata_cannot_change() -> None:
    book, _ = original_book()
    notice(book)
    with pytest.raises(ValueError, match="strictly after"):
        correction(book, "v2", 2, "v1", T0, CorrectionClass.BIG_R, HASH1)
    with pytest.raises(ValueError, match="cannot change"):
        book.publish_correction(
            FILING,
            "v2",
            ENTITY,
            FilingKind.TEN_K,
            1,
            PERIOD_END,
            2,
            HASH1,
            T3,
            "v1",
            CorrectionClass.LITTLE_R,
        )
    assert book.history(FILING) == (book.get_version("v1"),)


def test_hash_and_utc_validators_reject_malformed_values() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        FilingVersion(FILING, "v1", ENTITY, FilingKind.TEN_Q, 1, PERIOD_END, 1, "A" * 64, T0)
    with pytest.raises(ValueError, match="SHA-256"):
        FilingVersion(FILING, "v1", ENTITY, FilingKind.TEN_Q, 1, PERIOD_END, 1, "a" * 63, T0)
    for invalid in ("2025-04-01T00:00:00", "2025-04-01T00:00:00+00:00", "2025-04-01T00:00:00.0Z"):
        with pytest.raises(ValueError):
            FilingVersion(FILING, "v1", ENTITY, FilingKind.TEN_Q, 1, PERIOD_END, 1, HASH0, invalid)


def test_payload_boundary_and_public_collections_are_immutable() -> None:
    book, version = original_book()
    assert not hasattr(version, "payload")
    assert isinstance(book.versions, tuple)
    assert isinstance(book.non_reliance_notices, tuple)
    with pytest.raises(AttributeError):
        book.versions.append(version)  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        book.non_reliance_notices.append(  # type: ignore[attr-defined]
            NonRelianceNotice("n", ENTITY, ("v1",), T1, T2, "reason")
        )


def test_exact_four_direct_rules_are_registered() -> None:
    assert tuple(FILING_RULES) == (
        "PIT-FILING-VERSION",
        "PIT-NON-RELIANCE",
        "PIT-FILING-ASOF",
        "ACCT-RESTATEMENT-CLASS",
    )


# ---- M4.2A temporal closure -------------------------------------------------


def equity_book() -> E.EquityBook:
    ledger = L.Ledger("ENT-TEMPORAL")
    ledger.post(
        L.make(
            "open",
            "ENT-TEMPORAL",
            0,
            "opening",
            {"cash": "100.00"},
            {"common_stock": "100.00"},
            "2025-01-01T00:00:00Z",
            "2025-01-01T00:00:00Z",
        )
    )
    ledger.close(0, "2025-01-01T00:00:00Z")
    return E.EquityBook(ledger, E.CommonShareClass("common", Decimal("1.00")), issued_shares=100)


def test_equity_issuance_and_repurchase_reject_effective_date_event_date_disagreement() -> None:
    book = equity_book()
    before_entries = copy.deepcopy(book.ledger.entries)
    before_counts = book.share_counts()
    with pytest.raises(ValueError, match="effective_date"):
        book.issue_common_shares(
            1,
            "1.00",
            date(2025, 1, 2),
            1,
            "2025-01-01T00:00:00Z",
            "2025-01-01T01:00:00Z",
        )
    with pytest.raises(ValueError, match="effective_date"):
        book.repurchase_common_shares(
            1,
            "1.00",
            date(2025, 1, 2),
            1,
            "2025-01-01T00:00:00Z",
            "2025-01-01T01:00:00Z",
        )
    assert book.ledger.entries == before_entries
    assert book.share_counts() == before_counts


def test_equity_split_timestamp_pairing_order_and_date_are_checked_atomically() -> None:
    book = equity_book()
    before_events = book.events
    with pytest.raises(ValueError, match="both be present"):
        book.split_common_shares(2, date(2025, 1, 2), 1, "2025-01-02T00:00:00Z")
    with pytest.raises(ValueError, match="event_time <= posted_at"):
        book.split_common_shares(
            2,
            date(2025, 1, 2),
            1,
            "2025-01-02T01:00:00Z",
            "2025-01-02T00:00:00Z",
        )
    with pytest.raises(ValueError, match="effective_date"):
        book.split_common_shares(
            2,
            date(2025, 1, 2),
            1,
            "2025-01-01T00:00:00Z",
            "2025-01-01T01:00:00Z",
        )
    assert book.events == before_events
    assert book.share_counts() == E.ShareCounts(100, 0, 100)
