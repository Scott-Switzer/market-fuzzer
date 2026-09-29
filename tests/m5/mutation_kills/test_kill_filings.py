"""M5.3 killing tests for :mod:`fwf_kernel.filings`.

The filings book is the point-in-time record: what a reader could have relied on
at a given instant. Every rule in it is a statement about *time*, and every one of
them is invisible to a suite that only ever publishes a single original filing and
reads it back. The canonical suite did exactly that, so the following were
unobserved:

* the append-only chain -- contiguity, no branching, no duplicate ids, no second
  original, and the fields that are frozen for the life of a series;
* the BIG_R gate -- a restatement is only publishable once a non-reliance notice
  naming the superseded version is itself public, and the ordering between the
  two is the entire point of the rule;
* the as-of cutoffs -- ``version_status``, ``current_version`` and ``history`` all
  take an ``as_of`` instant, and a version or notice that becomes available later
  must not alter what an earlier query returned.

That last group is the one a point-in-time system exists to get right, and it is
the cheapest to get wrong: every test here would pass if the cutoffs were ignored
and the book simply reported its final state.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

import pytest
from fwf_kernel import filings as F

ENTITY = "ENT-KILL-FILINGS"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
PERIOD_END = date(2025, 12, 31)
#: Three distinct availability instants, strictly increasing.
T1 = "2026-02-01T00:00:00Z"
T2 = "2026-03-01T00:00:00Z"
T3 = "2026-04-01T00:00:00Z"


def raises_value(expected: str, call: Callable[[], object]) -> None:
    """Assert ``call`` raises ``ValueError`` whose message is exactly ``expected``."""
    with pytest.raises(ValueError) as info:
        call()
    assert str(info.value) == expected, f"message {str(info.value)!r} is not {expected!r}"


def book() -> F.FilingBook:
    return F.FilingBook()


def original(
    fb: F.FilingBook | None = None,
    filing: str = "F-1",
    available_at: str = T1,
    sha: str = SHA_A,
) -> F.FilingVersion:
    """Publish a version 1 into ``fb``, or into a throwaway book if none is given."""
    return (fb if fb is not None else book()).publish_original(
        filing_id=filing,
        version_id=f"{filing}-v1",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        payload_sha256=sha,
        available_at=available_at,
    )


def correction(
    fb: F.FilingBook,
    *,
    filing: str = "F-1",
    version: int = 2,
    supersedes: str | None = None,
    correction_class: str = "LITTLE_R",
    available_at: str = T2,
    sha: str = SHA_B,
    entity_id: str = ENTITY,
    form: str = "10-K",
    period: int = 1,
    period_end: date = PERIOD_END,
) -> F.FilingVersion:
    return fb.publish_correction(
        filing_id=filing,
        version_id=f"{filing}-v{version}",
        entity_id=entity_id,
        form=form,
        period=period,
        period_end=period_end,
        version=version,
        payload_sha256=sha,
        available_at=available_at,
        supersedes_version_id=supersedes,
        correction_class=correction_class,
    )


def notice(
    fb: F.FilingBook,
    *,
    notice_id: str = "N-1",
    affected: tuple[str, ...] = ("F-1-v1",),
    conclusion_at: str = T1,
    available_at: str = T2,
    entity_id: str = ENTITY,
    reason: str = "material error",
) -> F.NonRelianceNotice:
    return fb.declare_non_reliance(
        notice_id=notice_id,
        entity_id=entity_id,
        affected_version_ids=affected,
        conclusion_at=conclusion_at,
        available_at=available_at,
        reason=reason,
    )


# ================================================================ the chain
def test_a_filing_series_allows_only_one_original_version() -> None:
    """Kills the ``a filing series may have only one original version`` mutants.

    A series with two originals has no defined relationship between them: neither
    supersedes the other, so a reader cannot tell which is current. The version
    number is the only ordering the format provides, and two entries both numbered
    1 destroy it.
    """
    fb = book()
    original(fb)

    # A distinct version_id, so this reaches the one-original rule rather than
    # the duplicate-id rule that is checked first.
    raises_value(
        "a filing series may have only one original version",
        lambda: fb.publish_original(
            filing_id="F-1",
            version_id="F-1-v1b",
            entity_id=ENTITY,
            form="10-K",
            period=1,
            period_end=PERIOD_END,
            payload_sha256=SHA_B,
            available_at=T2,
        ),
    )


def test_a_corrected_version_needs_an_existing_series() -> None:
    """Kills the ``a corrected version requires an existing filing series`` mutants.

    A correction with nothing to correct is a version-2 entry with no version 1
    behind it, which leaves the chain with a hole and no way to resolve what a
    reader saw before the correction. The record itself is well formed here --
    ``supersedes_version_id`` is set -- so the rejection has to come from the
    book, not from the dataclass.
    """
    raises_value(
        "a corrected version requires an existing filing series",
        lambda: correction(book(), filing="F-NEW", version=2, supersedes="F-NEW-v1"),
    )


def test_a_corrected_version_needs_a_predecessor_that_exists() -> None:
    """Kills the ``supersedes_version_id does not identify an existing version`` mutants.

    Superseding an id that is not in the book produces a chain that cannot be
    walked, so no reader can determine the version in force at any instant.
    """
    fb = book()
    fb.publish_original(
        filing_id="F-1",
        version_id="F-1-v1",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        payload_sha256=SHA_A,
        available_at=T1,
    )

    raises_value(
        "supersedes_version_id does not identify an existing version",
        lambda: fb.publish_correction(
            filing_id="F-1",
            version_id="F-1-v2",
            entity_id=ENTITY,
            form="10-K",
            period=1,
            period_end=PERIOD_END,
            version=2,
            payload_sha256=SHA_B,
            available_at=T2,
            supersedes_version_id="F-1-v0",
            correction_class="LITTLE_R",
        ),
    )


def test_filing_version_numbers_must_be_contiguous() -> None:
    """Kills the ``filing version numbers must be contiguous`` mutants.

    Jumping from 1 to 3 leaves a version number that was never published, so
    ``current_version`` -- which reports the highest number in the series -- would
    report a version that skips one, and a reader auditing the chain would find a
    gap with no explanation.
    """
    fb = book()
    fb.publish_original(
        filing_id="F-1",
        version_id="F-1-v1",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        payload_sha256=SHA_A,
        available_at=T1,
    )

    raises_value(
        "filing version numbers must be contiguous",
        lambda: fb.publish_correction(
            filing_id="F-1",
            version_id="F-1-v3",
            entity_id=ENTITY,
            form="10-K",
            period=1,
            period_end=PERIOD_END,
            version=3,
            payload_sha256=SHA_B,
            available_at=T2,
            supersedes_version_id="F-1-v1",
            correction_class="LITTLE_R",
        ),
    )


def test_a_corrected_version_must_be_available_strictly_after_its_predecessor() -> None:
    """Kills the ``available strictly after`` strictness mutants.

    The bound is strict, and the instant is what orders the chain. A correction
    published at the same instant as the version it supersedes gives the two
    identical as-of answers, so there is no instant at which the original was in
    force -- which is precisely the record an auditor needs.
    """
    fb = book()
    fb.publish_original(
        filing_id="F-1",
        version_id="F-1-v1",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        payload_sha256=SHA_A,
        available_at=T1,
    )

    raises_value(
        "a corrected version must be available strictly after its predecessor",
        lambda: fb.publish_correction(
            filing_id="F-1",
            version_id="F-1-v2",
            entity_id=ENTITY,
            form="10-K",
            period=1,
            period_end=PERIOD_END,
            version=2,
            payload_sha256=SHA_B,
            available_at=T1,
            supersedes_version_id="F-1-v1",
            correction_class="LITTLE_R",
        ),
    )
    # One second later is accepted, which is what makes the bound strict.
    fb.publish_correction(
        filing_id="F-1",
        version_id="F-1-v2",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        version=2,
        payload_sha256=SHA_B,
        available_at="2026-02-01T00:00:01Z",
        supersedes_version_id="F-1-v1",
        correction_class="LITTLE_R",
    )


def test_a_filing_series_cannot_branch() -> None:
    """Kills the ``a filing series cannot branch`` mutants.

    Two versions both superseding version 1 is a fork: the chain stops being a
    chain, and ``current_version`` -- which takes the last one appended -- would
    silently pick a winner that no rule chose. The forked version gets its own
    id, so what is rejected here is the branch and not a duplicate key.
    """
    fb = book()
    v1 = original(fb)
    fb.publish_correction(
        filing_id="F-1",
        version_id="F-1-v2",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        version=2,
        payload_sha256=SHA_B,
        available_at=T2,
        supersedes_version_id=v1.version_id,
        correction_class="LITTLE_R",
    )

    raises_value(
        "a filing series cannot branch from a superseded version",
        lambda: fb.publish_correction(
            filing_id="F-1",
            version_id="F-1-v2b",
            entity_id=ENTITY,
            form="10-K",
            period=1,
            period_end=PERIOD_END,
            version=2,
            payload_sha256=SHA_C,
            available_at=T3,
            supersedes_version_id=v1.version_id,
            correction_class="LITTLE_R",
        ),
    )


def test_a_version_cannot_change_the_filing_it_belongs_to() -> None:
    """Kills the ``entity, form, period, and period_end cannot change`` mutants.

    A series is one filing: same entity, same form, same period. A correction that
    changes the period is not a correction of that filing, it is a different
    filing, and folding it into the series would make the period-indexed lookup
    return the wrong document.
    """
    fb = book()
    v1 = fb.publish_original(
        filing_id="F-1",
        version_id="F-1-v1",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        payload_sha256=SHA_A,
        available_at=T1,
    )

    raises_value(
        "entity, form, period, and period_end cannot change in a filing series",
        lambda: fb.publish_correction(
            filing_id="F-1",
            version_id="F-1-v2",
            entity_id=ENTITY,
            form="10-K",
            period=2,
            period_end=PERIOD_END,
            version=2,
            payload_sha256=SHA_B,
            available_at=T2,
            supersedes_version_id=v1.version_id,
            correction_class="LITTLE_R",
        ),
    )


def test_duplicate_version_ids_are_rejected() -> None:
    """Kills the ``duplicate filing version_id`` mutants.

    The version id is the primary key every point-in-time query resolves through,
    so a duplicate makes the book's answer depend on insertion order.
    """
    fb = book()
    original(fb)

    raises_value(
        "duplicate filing version_id: F-1-v1",
        lambda: fb.publish_original(
            filing_id="F-2",
            version_id="F-1-v1",
            entity_id=ENTITY,
            form="10-K",
            period=1,
            period_end=PERIOD_END,
            payload_sha256=SHA_B,
            available_at=T2,
        ),
    )


# ============================================================= the BIG_R gate
def test_a_big_r_correction_requires_a_public_non_reliance_notice() -> None:
    """Kills the ``a BIG_R correction requires a public non-reliance notice`` mutants.

    A restatement says the previous numbers should not be relied on. Publishing the
    restatement *before* the notice that withdraws reliance on the prior version
    leaves a window in which a reader sees the corrected filing and still holds the
    old one as reliable. The notice has to be public first.
    """
    fb = book()
    v1 = fb.publish_original(
        filing_id="F-1",
        version_id="F-1-v1",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        payload_sha256=SHA_A,
        available_at=T1,
    )

    raises_value(
        "a BIG_R correction requires a public non-reliance notice",
        lambda: fb.publish_correction(
            filing_id="F-1",
            version_id="F-1-v2",
            entity_id=ENTITY,
            form="10-K",
            period=1,
            period_end=PERIOD_END,
            version=2,
            payload_sha256=SHA_B,
            available_at=T2,
            supersedes_version_id=v1.version_id,
            correction_class="BIG_R",
        ),
    )
    # With the notice public first, the same correction is accepted.
    fb.declare_non_reliance(
        notice_id="N-1",
        entity_id=ENTITY,
        affected_version_ids=(v1.version_id,),
        conclusion_at=T1,
        available_at="2026-02-15T00:00:00Z",
        reason="material error",
    )
    fb.publish_correction(
        filing_id="F-1",
        version_id="F-1-v2",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        version=2,
        payload_sha256=SHA_B,
        available_at=T2,
        supersedes_version_id=v1.version_id,
        correction_class="BIG_R",
    )


def test_a_notice_that_names_another_version_does_not_unlock_a_big_r() -> None:
    """Kills the ``affected_version_ids`` membership mutants.

    A non-reliance notice withdraws reliance from the versions it names and no
    others. A notice naming some unrelated filing must not satisfy the gate for
    this one, or a restatement could be published with a notice that a reader
    following the actual affected version would never find.
    """
    fb = book()
    v1 = fb.publish_original(
        filing_id="F-1",
        version_id="F-1-v1",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        payload_sha256=SHA_A,
        available_at=T1,
    )
    other = fb.publish_original(
        filing_id="F-OTHER",
        version_id="F-OTHER-v1",
        entity_id=ENTITY,
        form="10-Q",
        period=1,
        period_end=PERIOD_END,
        payload_sha256=SHA_C,
        available_at=T1,
    )
    fb.declare_non_reliance(
        notice_id="N-1",
        entity_id=ENTITY,
        affected_version_ids=(other.version_id,),
        conclusion_at=T1,
        available_at="2026-02-15T00:00:00Z",
        reason="unrelated",
    )

    raises_value(
        "a BIG_R correction requires a public non-reliance notice",
        lambda: fb.publish_correction(
            filing_id="F-1",
            version_id="F-1-v2",
            entity_id=ENTITY,
            form="10-K",
            period=1,
            period_end=PERIOD_END,
            version=2,
            payload_sha256=SHA_B,
            available_at=T3,
            supersedes_version_id=v1.version_id,
            correction_class="BIG_R",
        ),
    )


def test_a_little_r_correction_needs_no_notice() -> None:
    """The counterpart: a revision is not a restatement.

    BIG_R and LITTLE_R have distinct structural requirements, and a rule that
    demanded a notice for both would make ordinary revisions unpublishable.
    """
    fb = book()
    v1 = fb.publish_original(
        filing_id="F-1",
        version_id="F-1-v1",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        payload_sha256=SHA_A,
        available_at=T1,
    )

    v2 = fb.publish_correction(
        filing_id="F-1",
        version_id="F-1-v2",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        version=2,
        payload_sha256=SHA_B,
        available_at=T2,
        supersedes_version_id=v1.version_id,
        correction_class="LITTLE_R",
    )

    assert v2.correction_class is F.CorrectionClass.LITTLE_R


# ======================================================= point-in-time status
def test_version_status_reports_reliable_as_filed_for_the_live_version() -> None:
    """The baseline status, before anything supersedes it."""
    fb = book()
    v1 = fb.publish_original(
        filing_id="F-1",
        version_id="F-1-v1",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        payload_sha256=SHA_A,
        available_at=T1,
    )

    assert fb.version_status(v1.version_id, T1) is F.FilingRelianceStatus.RELIABLE_AS_FILED


def test_a_superseded_version_reports_superseded() -> None:
    """Kills the ``SUPERSEDED`` branch in :meth:`version_status`.

    Once a successor is public, the prior version is no longer the one in force,
    and a reader asking about it must be told so rather than being told it is
    still reliable as filed.
    """
    fb = book()
    v1 = fb.publish_original(
        filing_id="F-1",
        version_id="F-1-v1",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        payload_sha256=SHA_A,
        available_at=T1,
    )
    fb.publish_correction(
        filing_id="F-1",
        version_id="F-1-v2",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        version=2,
        payload_sha256=SHA_B,
        available_at=T2,
        supersedes_version_id=v1.version_id,
        correction_class="LITTLE_R",
    )

    assert fb.version_status(v1.version_id, T2) is F.FilingRelianceStatus.SUPERSEDED


def test_a_non_reliance_notice_overrides_a_still_current_version() -> None:
    """Kills the ``NON_RELIANCE`` branch and its precedence over ``CURRENT_*``.

    A published notice withdraws reliance even from a version that has not been
    superseded and is otherwise the current restatement. The notice is the
    stronger signal, so it must be checked first; a book that reported
    ``CURRENT_RESTATED`` here would tell a reader the restated figures are safe to
    rely on after being told not to.
    """
    fb = book()
    v1 = original(fb)
    # The restatement is only publishable once the notice withdrawing reliance
    # from v1 is itself public, so that notice goes first.
    fb.declare_non_reliance(
        notice_id="N-0",
        entity_id=ENTITY,
        affected_version_ids=(v1.version_id,),
        conclusion_at="2026-02-15T00:00:00Z",
        available_at="2026-02-15T00:00:00Z",
        reason="material error",
    )
    v2 = fb.publish_correction(
        filing_id="F-1",
        version_id="F-1-v2",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        version=2,
        payload_sha256=SHA_B,
        available_at="2026-02-15T00:00:00Z",
        supersedes_version_id=v1.version_id,
        correction_class="BIG_R",
    )
    assert fb.version_status(v2.version_id, "2026-02-20T00:00:00Z") is (
        F.FilingRelianceStatus.CURRENT_RESTATED
    )

    fb.declare_non_reliance(
        notice_id="N-1",
        entity_id=ENTITY,
        affected_version_ids=(v2.version_id,),
        conclusion_at="2026-02-15T00:00:00Z",
        available_at="2026-02-20T00:00:00Z",
        reason="further error",
    )

    assert fb.version_status(v2.version_id, "2026-02-25T00:00:00Z") is (F.FilingRelianceStatus.NON_RELIANCE)


def test_a_future_version_cannot_change_an_earlier_as_of_result() -> None:
    """Kills the as-of cutoff mutants in :meth:`version_status`.

    The whole point of a point-in-time book: what was knowable at T1 does not
    change because something was published at T2. A version that is not yet
    available must be invisible, and the prior version must still read as
    reliable, at every instant before the successor's availability.
    """
    fb = book()
    v1 = fb.publish_original(
        filing_id="F-1",
        version_id="F-1-v1",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        payload_sha256=SHA_A,
        available_at=T1,
    )
    v2 = fb.publish_correction(
        filing_id="F-1",
        version_id="F-1-v2",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        version=2,
        payload_sha256=SHA_B,
        available_at=T2,
        supersedes_version_id=v1.version_id,
        correction_class="LITTLE_R",
    )

    # Before the successor exists, v2 is not knowable at all.
    assert fb.version_status(v2.version_id, T1) is None
    assert fb.version_status(v1.version_id, T1) is F.FilingRelianceStatus.RELIABLE_AS_FILED
    # The instant the successor becomes available, both flip together.
    assert fb.version_status(v2.version_id, T2) is F.FilingRelianceStatus.CURRENT_REVISED
    assert fb.version_status(v1.version_id, T2) is F.FilingRelianceStatus.SUPERSEDED


def test_a_future_notice_cannot_change_an_earlier_as_of_result() -> None:
    """Kills the as-of cutoff mutants on the notice side.

    A notice published at T3 must not withdraw reliance from a version as of T1.
    If it did, re-running a historical query after the fact would return a
    different answer than the one originally given, which is the failure mode
    point-in-time reporting exists to prevent.
    """
    fb = book()
    v1 = fb.publish_original(
        filing_id="F-1",
        version_id="F-1-v1",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        payload_sha256=SHA_A,
        available_at=T1,
    )
    fb.declare_non_reliance(
        notice_id="N-1",
        entity_id=ENTITY,
        affected_version_ids=(v1.version_id,),
        conclusion_at=T3,
        available_at=T3,
        reason="later error",
    )

    assert fb.version_status(v1.version_id, T1) is F.FilingRelianceStatus.RELIABLE_AS_FILED
    assert fb.version_status(v1.version_id, T3) is F.FilingRelianceStatus.NON_RELIANCE


def test_current_version_follows_the_as_of_instant() -> None:
    """Kills the cutoff mutants in :meth:`current_version`.

    Before the correction is public the series is at version 1; afterwards it is
    at version 2. A book that ignored the cutoff would report version 2 for a
    query dated before version 2 existed.
    """
    fb = book()
    v1 = fb.publish_original(
        filing_id="F-1",
        version_id="F-1-v1",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        payload_sha256=SHA_A,
        available_at=T1,
    )
    v2 = fb.publish_correction(
        filing_id="F-1",
        version_id="F-1-v2",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        version=2,
        payload_sha256=SHA_B,
        available_at=T2,
        supersedes_version_id=v1.version_id,
        correction_class="LITTLE_R",
    )

    assert fb.current_version("F-1", T1) == v1
    assert fb.current_version("F-1", T2) == v2


def test_history_is_truncated_at_the_as_of_instant() -> None:
    """Kills the cutoff mutants in :meth:`history`.

    The history of a series as of an instant must contain only what was public at
    that instant, in version order.
    """
    fb = book()
    v1 = fb.publish_original(
        filing_id="F-1",
        version_id="F-1-v1",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        payload_sha256=SHA_A,
        available_at=T1,
    )
    v2 = fb.publish_correction(
        filing_id="F-1",
        version_id="F-1-v2",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        version=2,
        payload_sha256=SHA_B,
        available_at=T2,
        supersedes_version_id=v1.version_id,
        correction_class="LITTLE_R",
    )

    assert fb.history("F-1", T1) == (v1,)
    assert fb.history("F-1", T2) == (v1, v2)
    assert fb.history("F-1") == (v1, v2)


# ================================================================ the notice
def test_a_notice_conclusion_cannot_follow_its_availability() -> None:
    """Kills the ``conclusion_at must be <= available_at`` mutants.

    A notice that is published before the company concluded anything says the
    conclusion was reached in the future. The bound is inclusive, so concluding
    and publishing at the same instant is fine.
    """
    fb = book()
    original(fb)

    raises_value(
        "conclusion_at must be <= available_at",
        lambda: notice(fb, conclusion_at=T2, available_at=T1),
    )


def test_a_notice_must_name_at_least_one_version() -> None:
    """Kills the ``affected_version_ids must not be empty`` mutants.

    A notice naming nothing withdraws reliance from nothing, while still reading
    to a consumer as a non-reliance notice having been published.
    """
    fb = book()
    original(fb)

    raises_value(
        "affected_version_ids must not be empty",
        lambda: notice(fb, affected=()),
    )


def test_a_notice_must_not_name_the_same_version_twice() -> None:
    """Kills the ``affected_version_ids must not contain duplicates`` mutants."""
    fb = book()
    original(fb)

    raises_value(
        "affected_version_ids must not contain duplicates",
        lambda: notice(fb, affected=("F-1-v1", "F-1-v1")),
    )


def test_an_original_version_may_not_claim_to_be_a_correction() -> None:
    """Kills the ``an original filing version cannot claim a correction`` mutants.

    Version 1 with a correction class is a restatement of nothing, and the
    distinction between BIG_R and LITTLE_R is exactly what the class is for.
    """
    raises_value(
        "an original filing version cannot claim a correction",
        lambda: book().publish_correction(
            filing_id="F-1",
            version_id="F-1-v1",
            entity_id=ENTITY,
            form="10-K",
            period=1,
            period_end=PERIOD_END,
            version=1,
            payload_sha256=SHA_A,
            available_at=T1,
            supersedes_version_id="F-0-v1",
            correction_class="LITTLE_R",
        ),
    )


# ====================================================== record validation
def test_a_notice_object_cannot_be_combined_with_field_arguments() -> None:
    """Kills the ``value is not None`` mutants in ``declare_non_reliance``.

    A notice is supplied either as a record or field by field, never both: the
    record is immutable and already validated, so silently dropping a field
    argument would make the caller believe an override took effect.
    """
    fb = book()
    v1 = original(fb)
    record = F.NonRelianceNotice(
        notice_id="N-R",
        entity_id=ENTITY,
        affected_version_ids=(v1.version_id,),
        conclusion_at=T1,
        available_at=T2,
        reason="material error",
    )

    with pytest.raises(TypeError, match="cannot be combined with field arguments"):
        fb.declare_non_reliance(record, entity_id=ENTITY)
    # A record alone is the supported spelling.
    assert fb.declare_non_reliance(record) is record


def test_a_notice_record_is_stored_under_its_own_id() -> None:
    """Kills the ``candidate = notice_id`` mutants.

    The book indexes notices by id; storing the id string itself instead of the
    record would make ``notices_for_version`` return strings, and every status
    query reading ``notice.available_at`` off them would raise.
    """
    fb = book()
    v1 = original(fb)
    record = F.NonRelianceNotice(
        notice_id="N-R",
        entity_id=ENTITY,
        affected_version_ids=(v1.version_id,),
        conclusion_at=T1,
        available_at=T2,
        reason="material error",
    )

    stored = fb.declare_non_reliance(record)

    assert stored is record
    assert fb.notices_for_version(v1.version_id) == (record,)
    assert fb.non_reliance_notices == (record,)


def test_an_unknown_filing_reports_no_current_version_and_empty_history() -> None:
    """Kills the ``.get(filing_id, ())`` default mutants in the queries.

    Querying a filing that was never published must answer "nothing", not
    raise: a reader asking about a series that does not exist yet is a normal
    point-in-time question. A ``None`` default (or a dropped one) would blow up
    inside the query instead of returning the empty answer.
    """
    fb = book()
    original(fb)

    assert fb.current_version("F-UNKNOWN", T2) is None
    assert fb.history("F-UNKNOWN") == ()
    assert fb.history("F-UNKNOWN", T2) == ()
    assert fb.version_status("F-UNKNOWN-v1", T2) is None


def test_a_version_status_query_on_a_series_with_no_successors() -> None:
    """Kills the ``.get`` default mutants inside ``version_status``.

    The successor scan runs on a version whose series exists but has nothing
    after it; an unreadable default there is the difference between "reliable as
    filed" and an internal error.
    """
    fb = book()
    v1 = original(fb)

    assert fb.version_status(v1.version_id, T2) is F.FilingRelianceStatus.RELIABLE_AS_FILED


def test_a_filing_version_coerces_and_stores_its_typed_fields() -> None:
    """Kills the ``__setattr__`` -> ``None`` mutants in ``FilingVersion``.

    The record normalises its fields on construction -- a string form becomes the
    enum member, a period end becomes a real ``date`` -- and every point-in-time
    query reads the stored field. Dropping the coerced value stores ``None``
    instead, which makes ``version_status`` and ``history`` compare against a
    null. Each coerced field is therefore read back off a published record.
    """
    version = original(book())

    assert version.form is F.FilingKind.TEN_K
    assert version.period_end == PERIOD_END
    assert version.period == 1
    assert version.version == 1
    assert version.available_at == T1


def test_a_correction_stores_its_correction_class_as_an_enum() -> None:
    """Kills the ``correction_class`` coercion mutants.

    BIG_R and LITTLE_R are distinct structural requirements, and the BIG_R gate
    is written against the enum member. A record that stored the raw string
    would fail that identity check, so a restatement's class is read back after
    publication.
    """
    fb = book()
    v1 = original(fb)

    v2 = fb.publish_correction(
        filing_id="F-1",
        version_id="F-1-v2",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        version=2,
        payload_sha256=SHA_B,
        available_at=T2,
        supersedes_version_id=v1.version_id,
        correction_class="LITTLE_R",
    )

    assert v2.correction_class is F.CorrectionClass.LITTLE_R


def test_an_original_version_must_not_supersede_or_classify() -> None:
    """Kills the ``or`` -> ``and`` mutants on the version-1 correction rule.

    Version 1 is the original, so it may carry neither a predecessor nor a
    correction class, and the two are independent: an original that names a
    predecessor, and an original that claims a class, each have to be refused.
    Folding the two clauses into an ``and`` admits the original that does both.
    """
    for kwargs in (
        {"supersedes_version_id": "F-0-v1"},
        {"correction_class": F.CorrectionClass.LITTLE_R},
    ):
        with pytest.raises(ValueError, match="an original filing version cannot claim"):
            F.FilingVersion(
                filing_id="F-1",
                version_id="F-1-v1",
                entity_id=ENTITY,
                form=F.FilingKind.TEN_K,
                period=1,
                period_end=PERIOD_END,
                version=1,
                payload_sha256=SHA_A,
                available_at=T1,
                **kwargs,  # type: ignore[arg-type]
            )


def test_a_corrected_version_needs_both_a_predecessor_and_a_class() -> None:
    """Kills the ``or`` -> ``and`` mutants on the version-N correction rule.

    Every version after the first has to name the version it supersedes and say
    how it differs, and the two requirements are independent: a version with a
    predecessor but no class, and a version with a class but no predecessor, are
    each rejected.
    """
    for kwargs in (
        {"supersedes_version_id": "F-1-v1"},
        {"correction_class": F.CorrectionClass.LITTLE_R},
    ):
        with pytest.raises(ValueError, match="requires supersession and classification"):
            F.FilingVersion(
                filing_id="F-1",
                version_id="F-1-v2",
                entity_id=ENTITY,
                form=F.FilingKind.TEN_K,
                period=1,
                period_end=PERIOD_END,
                version=2,
                payload_sha256=SHA_B,
                available_at=T2,
                **kwargs,  # type: ignore[arg-type]
            )


# ========================================================= value validators
def test_a_period_end_must_be_a_calendar_date() -> None:
    """Kills the ``_calendar_date`` ``or`` -> ``and`` mutants.

    A ``datetime`` is a subclass of ``date``, so an ``isinstance`` test alone
    would admit one, and a period end is a day rather than an instant. Folding
    the clauses together admits both a datetime and a non-date.
    """
    with pytest.raises(TypeError, match="period_end must be a date"):
        F._calendar_date(datetime(2025, 12, 31, 23, 59), "period_end")
    with pytest.raises(TypeError, match="period_end must be a date"):
        F._calendar_date("2025-12-31", "period_end")  # type: ignore[arg-type]
    assert F._calendar_date(PERIOD_END, "period_end") == PERIOD_END


def test_a_period_number_must_be_an_integer_at_least_one() -> None:
    """Kills the ``_integer`` ``or`` -> ``and`` mutants.

    A bool is an ``int`` in Python, a string is not an ``int`` at all, and
    numbering starts at 1. Each clause has to reject on its own.
    """
    raises_value("period must be an integer >= 1", lambda: F._integer(True, "period", 1))
    raises_value("period must be an integer >= 1", lambda: F._integer("1", "period", 1))  # type: ignore[arg-type]
    raises_value("period must be an integer >= 1", lambda: F._integer(0, "period", 1))
    assert F._integer(1, "period", 1) is None


def test_a_payload_digest_must_be_a_lowercase_sha256() -> None:
    """Kills the ``_sha256`` guard mutants.

    The digest is a commitment to content, so it has to be exactly 64 lowercase
    hex characters: uppercase hex, a short digest and a non-string all have to be
    refused, and each of the two clauses can refuse on its own.
    """
    message = "payload_sha256 must be an exact lowercase 64-character SHA-256"
    raises_value(message, lambda: F._sha256("A" * 64))
    raises_value(message, lambda: F._sha256("a" * 63))
    raises_value(message, lambda: F._sha256(123))  # type: ignore[arg-type]
    assert F._sha256("a" * 64) == "a" * 64


def test_a_version_object_cannot_be_combined_with_field_arguments() -> None:
    """Kills the ``value is not None`` mutants in ``_coerce_version``.

    A version is either supplied as a record or spelled out field by field, and
    accepting both silently discards one of them -- so a caller who meant to
    override a field on a record gets neither the record nor the override.
    """
    fb = book()
    record = original(fb)

    with pytest.raises(TypeError, match="cannot be combined with field arguments"):
        fb.publish_correction(record, version=2, entity_id=ENTITY)
    # Supplying the record alone is the supported spelling, and returns the
    # record rather than a rebuilt copy.
    spare = book()
    assert (
        spare.append_version(
            F.FilingVersion(
                filing_id="F-2",
                version_id="F-2-v1",
                entity_id=ENTITY,
                form=F.FilingKind.TEN_K,
                period=1,
                period_end=PERIOD_END,
                version=1,
                payload_sha256=SHA_A,
                available_at=T1,
            )
        ).version_id
        == "F-2-v1"
    )


def test_a_spelled_out_version_needs_every_required_field() -> None:
    """Kills the ``value is None`` mutants in ``_coerce_version``.

    A version spelled out field by field has to name all of its required fields:
    dropping any one of them produces a record with a null field rather than a
    rejection, and every point-in-time query then reads that null.
    """
    complete = {
        "version_id": "F-9-v1",
        "entity_id": ENTITY,
        "form": "10-K",
        "period": 1,
        "period_end": PERIOD_END,
        "payload_sha256": SHA_A,
        "available_at": T1,
    }
    for omitted in complete:
        fb = book()
        fields = {k: v for k, v in complete.items() if k != omitted}
        with pytest.raises(TypeError, match="all filing version fields are required"):
            fb.publish_original(filing_id="F-9", **fields)  # type: ignore[arg-type]
    assert original(book(), filing="F-9").filing_id == "F-9"


def test_a_correction_must_be_available_strictly_after_the_prior_version() -> None:
    """Kills the ``_instant``-label mutants inside the append-time check.

    The strict ordering between a version and its predecessor is checked with
    the same instant helper used everywhere else; rewriting its label there is
    invisible, but this assertion is what proves the *comparison* still sees the
    real availability stamps -- a correction at the same instant as its
    predecessor is refused.
    """
    fb = book()
    v1 = original(fb)

    raises_value(
        "a corrected version must be available strictly after its predecessor",
        lambda: correction(fb, supersedes=v1.version_id, available_at=T1),
    )
    # One second later is accepted, which is what makes the bound strict.
    assert correction(fb, supersedes=v1.version_id, available_at="2026-02-01T00:00:01Z")


def test_the_notice_index_serves_the_status_queries() -> None:
    """Kills the ``.get(version_id, ())`` default mutants on the notice index.

    A version with no notices must get an empty answer, not an internal error:
    most filings never attract a notice, so the empty case is the common case.
    """
    fb = book()
    v1 = original(fb)

    assert fb.notices_for_version(v1.version_id) == ()
    fb.declare_non_reliance(
        notice_id="N-1",
        entity_id=ENTITY,
        affected_version_ids=(v1.version_id,),
        conclusion_at=T1,
        available_at=T2,
        reason="material error",
    )
    assert len(fb.notices_for_version(v1.version_id)) == 1
    assert fb.notices_for_version("F-UNKNOWN-v1") == ()


def test_the_four_filing_rules_are_all_declared() -> None:
    """The rule vocabulary the book reports against, pinned by name.

    A rule id is what a caller keys on, so dropping or renaming one silently
    changes which documents a reconciliation run says are broken.
    """
    assert set(F.FILING_RULES) == {
        "PIT-FILING-VERSION",
        "PIT-NON-RELIANCE",
        "PIT-FILING-ASOF",
        "ACCT-RESTATEMENT-CLASS",
    }
    assert original(book()).filing_id == "F-1"


# ================================================== coercion + field guards
def test_a_record_object_can_be_published_without_field_arguments() -> None:
    """Kills ``_coerce_version`` mutant 2 (``value is not None`` flipped).

    The ``isinstance(record, FilingVersion)`` branch must accept a bare record
    when *no* field arguments accompany it: every field is ``None``, so the
    ``any(value is not None ...)`` scan is false and the record passes through.
    Flipping the membership test makes the all-``None`` scan true and the
    passthrough path raises -- a legal publish becomes impossible.
    """
    fb = book()
    original(fb, filing="F-REC")
    record = F.FilingVersion(
        filing_id="F-REC",
        version_id="F-REC-v2",
        entity_id=ENTITY,
        form="10-K",
        period=1,
        period_end=PERIOD_END,
        version=2,
        payload_sha256=SHA_B,
        available_at=T2,
        supersedes_version_id="F-REC-v1",
        correction_class=F.CorrectionClass.LITTLE_R,
    )
    published = fb.publish_correction(record)
    assert published.version_id == "F-REC-v2"
    assert fb.version_status("F-REC-v1", T2) is F.FilingRelianceStatus.SUPERSEDED


# ================================================== identity validation
def test_version_status_requires_a_non_empty_string_id() -> None:
    """Kills ``_text`` mutant 1 (``or`` widened to ``and``).

    A non-string id must be rejected with ``ValueError``; under the widened
    conjunction the non-string case instead crashes with ``AttributeError``
    on ``.strip``, and the empty-string case is not rejected at all.
    """
    fb = book()
    original(fb)
    raises_value("version_id must be a non-empty string", lambda: fb.version_status(123, T2))
    raises_value("version_id must be a non-empty string", lambda: fb.version_status("", T2))
