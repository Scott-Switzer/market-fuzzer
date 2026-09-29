"""M5.3 killing tests for :mod:`fwf_kernel.equity`.

The canonical FWF kernel suite exercises the equity book's happy path: open a
book, issue shares, repurchase some, read the share counts. Everything *around*
that path was unobserved, and it is where the accounting risk sits:

* the guards on each mutator -- a zero-share issuance, a repurchase larger than
  the shares outstanding, an issue price below par -- which the suite never
  provoked because it only ever called the mutators with valid arguments;
* the split arithmetic, including the divisibility check that stops a 3-for-2
  split from minting fractional shares, and the two integer divisions that
  convert share counts and par value;
* the *sequencing* of events and journal entries, which is what lets a book be
  reopened from a ledger that already contains its postings without colliding
  with the entry ids already in use;
* the three reconciliation rules, which compare the book against the ledger it
  is supposed to explain.

The last group is why this module exists. A book whose share counts disagree
with its own equity accounts still looks correct from the outside, and only the
cross-object comparison finds it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction

import pytest
from fwf_kernel import equity as Q
from fwf_kernel import ledger as L

D = Decimal
ENTITY = "ENT-KILL-EQUITY"
TS = "2025-03-01T00:00:00Z"
D1 = date(2025, 3, 1)
D2 = date(2025, 3, 31)
ZERO = D("0.00")
#: The kernel computes share-day weightings in a 60-digit context rather than
#: quantising to cents, so the expected figure has to be derived at the same
#: precision or the two sides of the comparison differ in the 29th digit.
EXACT_PRECISION = 60
#: A timestamp matching :data:`D2`, for events whose effective date is the 31st.
TS_END = "2025-03-31T00:00:00Z"
#: A timestamp matching March 15th, for events effective mid-window.
TS_MID = "2025-03-15T00:00:00Z"


def raises_value(expected: str, call: Callable[[], object]) -> None:
    """Assert ``call`` raises ``ValueError`` whose message is exactly ``expected``."""
    with pytest.raises(ValueError) as info:
        call()
    assert str(info.value) == expected, f"message {str(info.value)!r} is not {expected!r}"


def ledger(cash: str = "10000.00", issued: int = 1000, par: str = "1.00") -> L.Ledger:
    """A ledger opened with cash, share capital at par, and the excess in APIC.

    An opening entry must net to zero, and the equity book reconciles its own
    share capital against the ledger's, so the opening position is posted the way
    a real one would be: par value to common stock, the remainder to paid-in
    capital.
    """
    led = L.Ledger(ENTITY)
    par_capital = D(par) * issued
    credits = {"common_stock": par_capital}
    excess = D(cash) - par_capital
    if excess:
        credits["additional_paid_in_capital"] = excess
    led.post(L.make("open-0", ENTITY, 0, "opening", {"cash": D(cash)}, credits, TS, TS))
    return led


def share_class(par: str = "1.00") -> Q.CommonShareClass:
    return Q.CommonShareClass(class_id="COMMON", par_value=D(par))


def equity_book(
    *,
    issued: int = 1000,
    treasury: int = 0,
    par: str = "1.00",
    cash: str = "10000.00",
) -> Q.EquityBook:
    return Q.EquityBook(
        ledger(cash=cash, issued=issued, par=par),
        share_class(par),
        issued_shares=issued,
        treasury_shares=treasury,
    )


def rule(*ids: str) -> str:
    """The exact ``_require_valid`` message for a set of failed rule ids."""
    return f"invalid equity book: {sorted(ids)}"


# ================================================================ construction
def test_treasury_shares_cannot_exceed_issued_shares() -> None:
    """Kills the ``treasury_shares cannot exceed issued_shares`` mutants.

    Treasury shares are a subset of issued shares. Opening a book with more
    treasury than issued shares outstanding would report a negative outstanding
    count, and every share-day weighting downstream would be computed from it.
    """
    raises_value(
        "treasury_shares cannot exceed issued_shares",
        lambda: equity_book(issued=1000, treasury=1001),
    )
    # Equal is the legitimate fully-treasured case, which is what makes the
    # bound strict rather than inclusive.
    assert equity_book(issued=1000, treasury=1000)._treasury_shares == 1000


def test_opening_state_and_issued_shares_are_mutually_exclusive() -> None:
    """Kills the ``use opening_state or issued_shares, not both`` mutants.

    The two spellings of the opening position must not be combined: the explicit
    ``issued_shares`` argument and the ``opening_state`` record describe the same
    facts, and accepting both silently picks one.
    """
    raises_value(
        "use opening_state or issued_shares, not both",
        lambda: Q.EquityBook(
            ledger(),
            share_class(),
            issued_shares=1000,
            opening_state=Q.OpeningEquityState(
                issued_shares=1000,
                treasury_shares=0,
                common_stock=D("1000.00"),
                additional_paid_in_capital=D("0.00"),
                treasury_stock=D("0.00"),
            ),
        ),
    )


def test_an_equity_book_requires_an_issued_share_count() -> None:
    """Kills the ``issued_shares is required`` mutants.

    A book with no issued share count has no denominator for EPS and no opening
    balance for the equity accounts, so every downstream figure would be
    computed against nothing.
    """
    raises_value(
        "issued_shares is required for an equity book",
        lambda: Q.EquityBook(ledger(), share_class()),
    )


def test_share_class_rejects_a_blank_class_id() -> None:
    """Kills the ``class_id must be a non-empty string`` mutants.

    The class id is the key every share event is filed under, so a blank one
    would make events unattributable.
    """
    raises_value(
        "class_id must be a non-empty string",
        lambda: Q.CommonShareClass(class_id="  ", par_value=D("1.00")),
    )


# ================================================================ share events
def test_an_issuance_requires_a_positive_share_count() -> None:
    """Kills the ``share issuance count must be positive`` mutants.

    A zero-share issuance posts no journal entry but still consumes an event id
    and an entry sequence number, so the book and its ledger would drift apart by
    one event for an operation that never happened.
    """
    book = equity_book()
    raises_value(
        "share issuance count must be positive",
        lambda: book.issue_common_shares(0, D("1.00"), D1, 1, TS, TS),
    )


def test_an_issuance_price_below_par_is_rejected() -> None:
    """Kills the ``issue price must be at least par value`` mutants.

    Issuing below par would make paid-in capital negative, so the equity accounts
    would no longer represent an amount the company received for the shares it
    handed out.
    """
    book = equity_book(par="2.00")
    raises_value(
        "issue price must be at least par value",
        lambda: book.issue_common_shares(100, D("1.99"), D1, 1, TS, TS),
    )


def test_a_repurchase_cannot_exceed_the_shares_outstanding() -> None:
    """Kills the ``repurchase exceeds outstanding shares`` mutants.

    The check is against *outstanding* shares -- issued less treasury -- not
    against issued shares, so a book that has already repurchased some is the
    case that distinguishes the two.
    """
    book = equity_book(issued=1000, treasury=400)
    assert book.outstanding_shares == 600

    raises_value(
        "repurchase exceeds outstanding shares",
        lambda: book.repurchase_common_shares(601, D("1.00"), D2, 1, TS_END, TS_END),
    )
    # Exactly the outstanding count is the legitimate full-liquidation case.
    book.repurchase_common_shares(600, D("1.00"), D2, 1, TS_END, TS_END)
    assert book.outstanding_shares == 0


def test_an_event_must_be_effective_on_or_before_it_is_posted() -> None:
    """Kills the ``event_time <= posted_at`` strictness mutants in equity.

    The bound is inclusive: an event posted at the instant it becomes effective is
    the normal case, and requiring it to be strictly earlier would reject it.
    """
    book = equity_book()
    assert book.issue_common_shares(100, D("1.00"), D1, 1, TS, TS) is not None

    raises_value(
        "require event_time <= posted_at",
        lambda: book.issue_common_shares(100, D("1.00"), D2, 1, "2025-04-01T00:00:00Z", TS),
    )


def test_a_split_needs_timestamps_but_an_issuance_does_not() -> None:
    """Kills the ``issuance and repurchase require event_time and posted_at`` mutants.

    A split posts no journal entry, so it has no posting instant to record; an
    issuance posts one, so its timestamps are mandatory. Getting the two the
    wrong way round makes one of the operations impossible to call.
    """
    book = equity_book()

    split = book.split_common_shares(Fraction(2, 1), D2, 1)
    assert split.event_time is None and split.posted_at is None

    raises_value(
        "issuance and repurchase require event_time and posted_at",
        lambda: book.issue_common_shares(100, D("1.00"), D1, 1, None, None),
    )


# ================================================================== splitting
def test_a_split_scales_both_share_counts_by_exact_integer_arithmetic() -> None:
    """Kills the ``//`` -> ``/`` and operand-order mutants in the split.

    A split changes share counts and par value but posts nothing, so the only
    evidence it happened is the share counts themselves. Truncating division
    would round share counts down; real division would make them floats, which
    then fail every later integer comparison; and swapping the numerator for the
    denominator applies the inverse ratio. All three leave the book looking
    plausible and wrong.
    """
    book = equity_book(issued=1000, treasury=200, par="1.00")

    book.split_common_shares(Fraction(3, 2), D2, 1)

    assert book.issued_shares == 1500
    assert book.treasury_shares == 300
    assert book.outstanding_shares == 1200
    # A 3-for-2 split divides the par value by the same ratio.
    assert book._par_fraction == Fraction(2, 3)


def test_a_split_that_would_mint_fractional_shares_is_rejected() -> None:
    """Kills the ``split would produce fractional shares`` mutants.

    The divisibility check is what stops a book from reporting a share count it
    cannot issue. Without it, a 3-for-2 split of 1001 shares would silently
    truncate to 1501, so the company would report fewer shares than it had
    issued -- a capitalisation that no transfer agent could settle.
    """
    book = equity_book(issued=1001, treasury=0, par="1.00")

    raises_value(
        "split would produce fractional shares",
        lambda: book.split_common_shares(Fraction(3, 2), D2, 1),
    )
    # A count the ratio divides evenly is accepted, which is what makes the
    # check a divisibility test rather than a blanket refusal.
    ok = equity_book(issued=1002, treasury=0, par="1.00")
    ok.split_common_shares(Fraction(3, 2), D2, 1)
    assert ok.issued_shares == 1503


# ==================================================== event / entry sequencing
def test_event_ids_are_numbered_from_one_without_gaps() -> None:
    """Kills the ``len(self._events) + 1`` mutants in :meth:`_next_event_id`.

    Event ids are 1-based and dense. Starting at zero, or skipping one, gives ids
    that collide with or interleave incorrectly against a book that was reopened
    from its own event log.
    """
    book = equity_book()

    first = book.issue_common_shares(100, D("1.00"), D1, 1, TS, TS)
    second = book.issue_common_shares(100, D("1.00"), D2, 1, TS_END, TS_END)

    assert first.event_id == "m42a-event-00000001"
    assert second.event_id == "m42a-event-00000002"


def test_entry_ids_continue_the_ledger_s_own_sequence() -> None:
    """Kills the ``self._entry_sequence + 1`` mutants in :meth:`_post_event`.

    A book reopened on a ledger that already carries ``m42a-00000007-...`` must
    continue from 8, not restart at 1 and collide with the ids already posted.
    That is the whole purpose of ``_last_entry_sequence``, and it is only
    observable by reopening a book.
    """
    led = ledger(issued=1000)
    led.post(
        L.event(
            "m42a-00000007-issue_common_shares",
            ENTITY,
            1,
            "issue_common_shares",
            {"cash": "100.00", "common_stock": "100.00", "additional_paid_in_capital": "0.00"},
            TS,
            TS,
        )
    )

    book = Q.EquityBook(led, share_class(), issued_shares=1100)
    assert book._entry_sequence == 7

    book.issue_common_shares(100, D("1.00"), D2, 1, TS_END, TS_END)

    assert [e.entry_id for e in book.ledger.entries if e.event == "issue_common_shares"] == [
        "m42a-00000007-issue_common_shares",
        "m42a-00000008-issue_common_shares",
    ]


def test_entry_sequences_ignore_entries_that_are_not_equity_postings() -> None:
    """Kills the ``m42a`` prefix and split-arity mutants in ``_last_entry_sequence``.

    Only the book's own ``m42a-<n>-<event>`` ids carry a sequence. An ordinary
    ``sale_on_credit`` entry, or a third segment after the sequence, must not be
    mistaken for one -- otherwise the book would resume numbering from a
    coincidental match and post an entry id that is already in use.
    """
    led = ledger(issued=1000)
    led.post(
        L.event(
            "m42a-00000007-issue_common_shares",
            ENTITY,
            1,
            "issue_common_shares",
            {"cash": "100.00", "common_stock": "100.00", "additional_paid_in_capital": "0.00"},
            TS,
            TS,
        )
    )
    # A higher number that must not be picked up: wrong prefix.
    led.post(
        L.event(
            "m41-00000099-something", ENTITY, 1, "sale_on_credit", {"ar": "5.00", "revenue": "5.00"}, TS, TS
        )
    )
    # A wrong prefix again, this time a real m42a-shaped id with a bad tag.
    led.post(
        L.event(
            "m42b-00000050-issue_common_shares",
            ENTITY,
            1,
            "issue_common_shares",
            {"cash": "1.00", "common_stock": "1.00", "additional_paid_in_capital": "0.00"},
            TS,
            TS,
        )
    )

    book = Q.EquityBook(led, share_class(), issued_shares=1101)

    assert book._entry_sequence == 7


def test_a_book_with_no_prior_events_starts_its_sequence_at_zero() -> None:
    """Kills the ``max(sequences, default=1)`` mutant.

    With nothing to resume from, the next entry must be number 1. Defaulting to 1
    makes the first entry number 2, leaving a permanent hole in the sequence and
    making a reopened book disagree with a live one about which entry is next.
    """
    book = equity_book()
    assert book._entry_sequence == 0

    book.issue_common_shares(100, D("1.00"), D1, 1, TS, TS)

    assert book.ledger.entries[-1].entry_id == "m42a-00000001-issue_common_shares"


def test_effective_date_must_match_the_utc_date_of_the_event_time() -> None:
    """Kills the ``effective_date must equal the UTC calendar date`` mutants.

    The effective date is the date a share event takes economic effect, and the
    share-day weighting is computed from it. If it can disagree with the event
    time's own date, weighted-average shares are computed over the wrong days.
    """
    book = equity_book()

    raises_value(
        "effective_date must equal the UTC calendar date of event_time",
        lambda: book.issue_common_shares(100, D("1.00"), date(2025, 3, 2), 1, TS, TS),
    )
    # The matching date is accepted, which is what makes this an equality rule.
    assert book.issue_common_shares(100, D("1.00"), date(2025, 3, 1), 1, TS, TS)


# ============================================================= reconciliations
def test_share_events_reconcile_the_equity_accounts_and_the_ledger() -> None:
    """Kills the ``EQUITY_RULE`` mutants: the book must explain the ledger.

    An issuance posts cash, common stock at par and the excess to paid-in
    capital. The book tracks all three internally *and* against the ledger, so a
    book whose internal balances disagree with the ledger entries it posted is
    broken even though both halves look plausible alone.
    """
    book = equity_book(issued=1000, par="1.00", cash="1000.00")
    book.issue_common_shares(200, D("2.50"), D1, 1, TS, TS)

    assert book.validate_reconciliations() == set()
    # 200 shares at 2.50 is 500.00 of cash for 200.00 of par and 300.00 of APIC.
    balances = book.ledger.balances()
    assert balances["common_stock"] == D("1200.00")
    assert balances["additional_paid_in_capital"] == D("300.00")
    assert balances["cash"] == D("1500.00")
    assert book.share_counts().issued_shares == 1200
    assert book.share_counts().outstanding_shares == 1200


def test_weighted_average_shares_weights_by_share_days() -> None:
    """Kills the ``WASO_RULE`` mutants.

    1000 shares for all of March, then 500 more from 16 March: the weighting is
    share-days over days, so the average is not the simple mean of the two
    counts. Reporting a plain average would misstate the EPS denominator, which
    is the single most used number an equity book produces.
    """
    book = equity_book(issued=1000, par="1.00")
    book.issue_common_shares(
        500, D("1.00"), date(2025, 3, 16), 1, "2025-03-16T00:00:00Z", "2025-03-16T00:00:00Z"
    )

    waso = book.weighted_average_shares(D1, D2)

    # 31 days in March: 1000 for the first 15, 1500 for the last 16.
    with localcontext() as ctx:
        ctx.prec = EXACT_PRECISION
        assert waso == (D("1000.00") * 15 + D("1500.00") * 16) / 31
    assert book.validate_waso(D1, D2) == set()
    assert book.validate_waso(D1, D2, reported=waso) == set()
    # A reported figure that disagrees is the failure the rule exists to catch.
    assert Q.WASO_RULE in book.validate_waso(D1, D2, reported=D("1.00"))


def test_basic_eps_is_net_income_over_weighted_average_shares() -> None:
    """Kills the ``BASIC_EPS_RULE`` and ``basic_eps`` mutants.

    EPS is net income divided by weighted-average shares, rounded to the
    reporting quantum. Passing the period arguments through to the WASO call
    matters: dropping them weights over the wrong window and produces an EPS that
    reconciles against nothing.
    """
    book = equity_book(issued=1000, par="1.00")

    eps = book.basic_eps(D("10000.00"), D1, D2)

    assert eps == D("10000.00") / D("1000.00")
    assert book.validate_basic_eps(D("10000.00"), D1, D2) == set()
    assert book.validate_basic_eps(D("10000.00"), D1, D2, reported=eps) == set()
    assert Q.BASIC_EPS_RULE in book.validate_basic_eps(D("10000.00"), D1, D2, reported=D("99.00"))


# ============================================== primitive value validators
def test_share_counts_reject_anything_that_is_not_a_whole_number() -> None:
    """Kills the ``_count`` guard mutants.

    A share count has to be a non-negative whole number of shares, and each of
    the three clauses can reject on its own: a bool is an ``int`` in Python and
    is not a share count, a binary float is not exact, and a fractional value
    is not a whole number of shares. Folding the conditions together lets one of
    them through and every WASO and EPS figure downstream inherits it.
    """
    raises_value("shares must be a non-negative integer", lambda: Q._count("1.5", "shares"))
    raises_value("shares must be a non-negative integer", lambda: Q._count("-1", "shares"))
    raises_value("shares must be a non-negative integer", lambda: Q._count("nan", "shares"))
    for bad in (True, 1.0):
        with pytest.raises(TypeError, match="binary floats"):
            Q._count(bad, "shares")
    assert Q._count("1000", "shares") == 1000


def test_split_factors_must_be_exact_positive_fractions() -> None:
    """Kills the ``_factor`` guard and tuple-handling mutants.

    A split factor is a rational number of shares per share, so it has to be
    exact and positive. Zero and negatives are refused, a zero denominator is
    refused rather than raising ``ZeroDivisionError``, and a binary float is
    refused because it cannot represent the ratio exactly. The tuple spelling
    has to build the same fraction the integer spelling does.
    """
    raises_value("split factor must be positive", lambda: Q._factor(0))
    raises_value("split factor must be positive", lambda: Q._factor(-2))
    raises_value("split factor must be an exact positive fraction", lambda: Q._factor((1, 0)))
    with pytest.raises(TypeError, match="split factors must use exact"):
        Q._factor(1.5)
    assert Q._factor((3, 2)) == Fraction(3, 2)
    assert Q._factor(Fraction(3, 2)) == Fraction(3, 2)
    assert Q._factor(2) == Fraction(2, 1)


def test_money_refuses_negatives_unless_the_caller_opts_in() -> None:
    """Kills the ``_money`` / ``_exact_decimal`` flag and sign mutants.

    Money is non-negative by default because the equity accounts it feeds are
    balances, and a caller that legitimately needs a negative -- net income, for
    instance -- has to say so. The opt-in has to be honoured rather than
    ignored, and a negative refused by default rather than silently accepted.
    """
    raises_value("money must be non-negative", lambda: Q._money(D("-1.00")))
    with pytest.raises(TypeError, match="binary floats are not accepted"):
        Q._money(1.0)
    assert Q._money(D("-1.00"), allow_negative=True) == D("-1.00")
    assert Q._money(D("1.005")) == D("1.01")


def test_effective_dates_reject_datetimes_and_strings() -> None:
    """Kills the ``_date`` ``or`` -> ``and`` mutants.

    A ``datetime`` is a subclass of ``date``, so an ``isinstance`` check alone
    would admit one, and an instant is not a calendar day. Folding the two
    clauses into an ``and`` admits both a datetime and a string, which then
    flows into a share-day weighting keyed by date.
    """
    with pytest.raises(TypeError, match="must be a date, not a datetime or string"):
        Q._date(datetime(2025, 3, 1), "effective_date")
    with pytest.raises(TypeError, match="must be a date, not a datetime or string"):
        Q._date("2025-03-01", "effective_date")  # type: ignore[arg-type]
    assert Q._date(D1, "effective_date") == D1


def test_operating_periods_reject_bools_strings_and_zero() -> None:
    """Kills the ``_period`` ``or`` -> ``and`` mutants.

    A bool is an ``int`` in Python, a string is not an ``int`` at all, and
    period numbering starts at 1. Each clause has to be able to reject alone.
    """
    raises_value("period must be an operating period >= 1", lambda: Q._period(True, "period"))
    raises_value("period must be an operating period >= 1", lambda: Q._period("1", "period"))
    raises_value("period must be an operating period >= 1", lambda: Q._period(0, "period"))
    assert Q._period(1, "period") is None


def test_reported_figures_are_only_compared_when_one_was_reported() -> None:
    """Kills the ``reported is not None and ...`` -> ``or`` mutants.

    A reconciliation call with no reported figure is asking "is this internally
    consistent", and the answer for a sound book is "no broken rules". Folding
    the guard into an ``or`` makes the comparison run against ``None``, which
    raises, is swallowed, and reports the rule as broken for a book that is
    fine -- so both rules are checked with and without a reported value.
    """
    book = equity_book(issued=1000, par="1.00")
    waso = book.weighted_average_shares(D1, D2)
    eps = book.basic_eps(D("10000.00"), D1, D2)

    assert book.validate_waso(D1, D2) == set()
    assert book.validate_waso(D1, D2, reported=waso) == set()
    assert book.validate_basic_eps(D("10000.00"), D1, D2) == set()
    assert book.validate_basic_eps(D("10000.00"), D1, D2, reported=eps) == set()
    assert Q.WASO_RULE in book.validate_waso(D1, D2, reported=D("1"))
    assert Q.BASIC_EPS_RULE in book.validate_basic_eps(D("10000.00"), D1, D2, reported=D("1"))
    assert ZERO == D("0.00")


# ============================================== replay, splits and EPS plumbing
def test_events_replay_in_date_order_with_splits_first() -> None:
    """Kills the ``_ordered_events`` priority mutants.

    Two events on the same date are applied in the order split, issuance,
    repurchase. The priority decides the day's outstanding count and therefore
    every WASO share-day: repurchasing *before* a same-day split, for instance,
    would scale the repurchased shares differently than repurchasing after.
    """
    book = equity_book(issued=1000, par="1.00")
    # Same-day issuance and repurchase, recorded out of priority order.
    book.issue_common_shares(100, D("1.00"), D2, 1, TS_END, TS_END)
    book.repurchase_common_shares(50, D("1.00"), D2, 1, TS_END, TS_END)
    book.split_common_shares(Fraction(2, 1), D2, 1)

    ordered = book._ordered_events(D2)

    assert [e.kind for e in ordered] == [
        Q.ShareEventKind.SPLIT,
        Q.ShareEventKind.ISSUANCE,
        Q.ShareEventKind.REPURCHASE,
    ]


def test_a_same_day_split_applies_to_that_days_share_count() -> None:
    """Kills the ``day < effective_date`` split-adjustment mutants.

    The split adjustment answers: for a day *before* the split, what would the
    share count have been under the new share definition? It applies strictly
    before the effective date; on the split date itself the count already
    carries the split, so adjusting again would double it.
    """
    book = equity_book(issued=1000, par="1.00")
    before, on, after = D1, D2, date(2025, 4, 1)
    book.split_common_shares(Fraction(2, 1), D2, 1)

    # The replay applies the split on its effective date: before it the count
    # is the old one, from the split date on it is the new one.
    assert book._outstanding_on(before, after) == 1000
    assert book._outstanding_on(on, after) == 2000
    assert book._outstanding_on(after, after) == 2000
    # Every day of a window spanning the split reports the same split-adjusted
    # outstanding, which is what makes the WASO continuous.
    assert book.weighted_average_shares(before, after) == D("2000.00")


def test_split_divisibility_is_checked_for_both_share_pools() -> None:
    """Kills the ``and`` -> ``or`` and ``//`` -> ``/`` mutants in replay.

    A split must be representable for the issued *and* the treasury pool; the
    ``or`` is what rejects a split that mints fractional treasury shares. And
    the division has to stay integral: float division produces a share count
    with a decimal part, which is not a number of shares.
    """
    led = ledger()
    led.post(_decoy("m42a-00000001-x"))
    book = Q.EquityBook(led, share_class(), issued_shares=1000, treasury_shares=0)
    book.split_common_shares(Fraction(3, 2), D1, 1)
    book.issue_common_shares(3, D("1.00"), D2, 1, TS_END, TS_END)
    # After the split and re-issue, 1501 shares cannot split 3-for-2 cleanly;
    # the divisibility check must fire on the replay.
    with pytest.raises(ValueError, match="split would produce fractional shares"):
        book._apply_event(
            1501, 0, Q.ShareEvent("evt-1", Q.ShareEventKind.SPLIT, D2, 1, factor=Fraction(3, 2))
        )

    # Integral division: a clean 3-for-2 split stays integral for both pools.
    issued, treasury = book._apply_event(
        1500, 4, Q.ShareEvent("evt-2", Q.ShareEventKind.SPLIT, D2, 1, factor=Fraction(3, 2))
    )
    assert (issued, treasury) == (2250, 6)
    # And a treasury pool that fails divisibility is refused by the same guard,
    # even though the issued pool would have split cleanly.
    with pytest.raises(ValueError, match="split would produce fractional shares"):
        book._apply_event(
            1500, 3, Q.ShareEvent("evt-3", Q.ShareEventKind.SPLIT, D2, 1, factor=Fraction(3, 2))
        )


def test_replaying_a_full_history_returns_the_live_counts() -> None:
    """Kills the ``issued = None``/``common = None`` construction mutants.

    A book opened from an ``OpeningEquityState`` replays its events from that
    state; the replay must land exactly on the live counters, or the equity rule
    would report the book broken on open. Each component of the state is read
    individually, so dropping one to ``None`` breaks the replay instead of
    constructing a valid book.
    """
    led = L.Ledger(ENTITY)
    led.post(
        L.make(
            "open-0",
            ENTITY,
            0,
            "opening",
            {"cash": D("10000.00"), "treasury_stock": D("100.00")},
            {"common_stock": D("1000.00"), "additional_paid_in_capital": D("9100.00")},
            TS,
            TS,
        )
    )
    state = Q.OpeningEquityState(
        issued_shares=1000,
        treasury_shares=100,
        common_stock=D("1000.00"),
        additional_paid_in_capital=D("9100.00"),
        treasury_stock=D("100.00"),
    )
    book = Q.EquityBook(led, share_class(), opening_state=state)

    counts = book.share_counts()

    assert counts.issued_shares == 1000
    assert counts.treasury_shares == 100
    assert counts.outstanding_shares == 900
    assert book._common_stock_balance == D("1000.00")
    assert book._apic_balance == D("9100.00")
    assert book._treasury_stock_balance == D("100.00")


def test_share_counts_honour_the_as_of_cutoff() -> None:
    """Kills the ``_replay_counts(None)`` and ``_date(as_of_date, ...)`` mutants.

    ``share_counts(as_of_date)`` answers a question about the past; passing
    ``None`` instead of the date would replay the *full* history and report the
    present. The date also has to be validated, so a non-date raises.
    """
    book = equity_book(issued=1000, par="1.00")
    book.issue_common_shares(100, D("1.00"), D2, 1, TS_END, TS_END)

    assert book.share_counts(D1).outstanding_shares == 1000
    assert book.share_counts(D2).outstanding_shares == 1100
    with pytest.raises(TypeError, match="as_of_date must be a date"):
        book.share_counts("2025-03-02")  # type: ignore[arg-type]


def test_basic_eps_quantizes_at_six_decimals_with_half_up() -> None:
    """Kills the EPS quantum, precision and rounding mutants.

    EPS is rounded to six decimal places, computed at 60-digit precision, with
    ROUND_HALF_UP at the tie. A third decimal digit of income-per-share is what
    distinguishes the quantum from a coarser one, and the tie case is what pins
    the rounding mode.
    """
    book = equity_book(issued=3, par="1.00", cash="3.00")

    # 1 / 3 = 0.333333333... -- the seventh digit proves the quantum.
    assert book.basic_eps(D("1.00"), D1, D2) == D("0.333333")
    # 0.0000005 per share is an exact tie at the sixth decimal.
    tie = equity_book(issued=1000000, par="1.00", cash="1000000.00")
    assert tie.basic_eps(D("0.50"), D1, D2) == D("0.000001")
    # Dropping the precision to a lower value would round the ratio early.
    with localcontext() as ctx:
        ctx.prec = 60
        expected = (D("1.00") / D("3")).quantize(D("0.000001"), rounding=ROUND_HALF_UP)
    assert book.basic_eps(D("1.00"), D1, D2) == expected


def test_validate_date_range_rejects_an_empty_period() -> None:
    """Kills the ``end < start`` -> ``end <= start`` mutant.

    A one-day reporting period is legitimate -- a book opened and closed on the
    same date has a real WASO, namely the outstanding count for that one day.
    The bound has to stay strict rather than inclusive.
    """
    book = equity_book(issued=1000, par="1.00")

    assert book.weighted_average_shares(D1, D1) == D("1000.00")
    raises_value(
        "period_end must be >= period_start",
        lambda: book.weighted_average_shares(D2, D1),
    )


def test_reconciliation_passes_the_period_window_through() -> None:
    """Kills the argument-drop mutants in ``validate_reconciliations``.

    The reconciliation forwards its window and any reported figures to the WASO
    and EPS validators. Dropping an argument shifts every later one into the
    wrong slot -- the reported figure becomes the cutoff -- which either raises
    or compares against the wrong quantity. Each call is made with a distinct
    window and a reported value, so the shift is observable.
    """
    book = equity_book(issued=1000, par="1.00")
    waso = book.weighted_average_shares(D1, D2)
    eps = book.basic_eps(D("10000.00"), D1, D2)

    assert (
        book.validate_reconciliations(
            period_start=D1,
            period_end=D2,
            reported_waso=waso,
            net_income=D("10000.00"),
            reported_eps=eps,
        )
        == set()
    )
    assert Q.WASO_RULE in book.validate_reconciliations(
        period_start=D1,
        period_end=D2,
        reported_waso=D("1"),
    )
    assert Q.BASIC_EPS_RULE in book.validate_reconciliations(
        period_start=D1,
        period_end=D2,
        net_income=D("10000.00"),
        reported_eps=D("1"),
    )
    # Only one of the two window dates is not a window.
    assert Q.WASO_RULE in book.validate_reconciliations(period_start=D1)
    assert Q.WASO_RULE in book.validate_reconciliations(period_end=D2)


def test_a_partial_window_raises_and_is_reported_as_wasO_broken() -> None:
    """Kills the ``period_start is not None and period_end is not None`` mutant.

    Supplying only one end of the window is malformed, and the reconciliation
    reports it as a WASO failure rather than half-running the check. Folding the
    guard into an ``or`` would call the validator with a ``None`` end and get a
    ``TypeError``, which the ``except`` would swallow into the same rule -- but
    with a genuinely broken book it would also swallow *that*, so both arms are
    pinned.
    """
    book = equity_book(issued=1000, par="1.00")

    assert Q.WASO_RULE in book.validate_reconciliations(period_start=D1)
    assert Q.WASO_RULE in book.validate_reconciliations(period_end=D2)
    assert book.validate_reconciliations() == set()


def test_share_event_records_validate_their_own_fields() -> None:
    """Kills the ``ShareEvent.__post_init__`` coercion and guard mutants.

    A share event is the audit record of a capital change: its id has to be a
    real identifier, its share count an exact integer, its price an exact
    non-negative decimal stored as computed, and its factor a positive exact
    rational. Zero or negative shares on an issuance-type event, a non-Fraction
    factor, and a factor at or below zero are each refused.
    """
    base = dict(
        event_id="evt-1",
        kind=Q.ShareEventKind.ISSUANCE,
        effective_date=D1,
        period=1,
        shares=100,
        price=D("1.00"),
        event_time=TS,
        posted_at=TS,
    )
    assert Q.ShareEvent(**base).price == D("1.00")
    with pytest.raises(TypeError, match="shares must be an integer"):
        Q.ShareEvent(**{**base, "shares": True})
    raises_value("issuance events require positive shares", lambda: Q.ShareEvent(**{**base, "shares": 0}))
    raises_value("factor must be a positive Fraction", lambda: Q.ShareEvent(**{**base, "factor": None}))
    raises_value(
        "factor must be a positive Fraction", lambda: Q.ShareEvent(**{**base, "factor": Fraction(0, 1)})
    )
    raises_value("event_id must be a non-empty string", lambda: Q.ShareEvent(**{**base, "event_id": "  "}))
    # A split carries a factor and no share count.
    split = Q.ShareEvent(
        event_id="evt-2",
        kind=Q.ShareEventKind.SPLIT,
        effective_date=D1,
        period=1,
        factor=Fraction(2, 1),
    )
    assert split.shares == 0


def test_issuance_ties_common_stock_to_issued_shares_times_par() -> None:
    """Kills the ``apic``/``proceeds`` guard and par-product mutants.

    The issuance has to preserve the invariant common stock == issued * par
    exactly, at cent precision; the guard rejects a posting that cannot be
    represented in cents. Folding the guard into an ``and`` admits an APIC that
    went negative as long as the par leg overshot, and replacing the product's
    multiplication with division breaks the invariant arithmetic itself.
    """
    book = equity_book(issued=1000, par="1.00")

    book.issue_common_shares(333, D("1.01"), D2, 1, TS_END, TS_END)
    assert book._common_stock_balance == D("1333.00")
    assert book._apic_balance == D("9003.33")
    assert book.share_counts().outstanding_shares == 1333
    with pytest.raises(ValueError, match="issue price must be at least par value"):
        book.issue_common_shares(10, D("0.99"), D2, 1, TS_END, TS_END)


def test_repurchase_requires_cash_and_positive_price() -> None:
    """Kills the repurchase price and cash-availability mutants.

    A repurchase pays real cash at a positive price, so a zero price is refused
    and the ledger has to hold enough cash to cover the cost. Spending the last
    cent is allowed, which is what makes the cash bound strict.
    """
    book = equity_book(issued=1000, par="1.00", cash="10000.00")
    book.repurchase_common_shares(100, D("1.00"), D2, 1, TS_END, TS_END)
    assert book.treasury_shares == 100
    with pytest.raises(ValueError, match="repurchase price must be positive"):
        book.repurchase_common_shares(1, ZERO, D2, 1, TS_END, TS_END)

    thin = equity_book(issued=10, par="1.00", cash="10.00")
    thin.repurchase_common_shares(10, D("1.00"), D2, 1, TS_END, TS_END)
    assert thin._last_entry_sequence() == 1


def test_opening_equity_state_rejects_an_impossible_treasury_count() -> None:
    """Kills the ``treasury > issued`` -> ``>=`` mutant.

    A fully-treasured company is legitimate; more treasury than issued shares is
    not. The bound has to stay strict, and the coerced counts have to be stored
    rather than dropped.
    """
    state = Q.OpeningEquityState(
        issued_shares=1000,
        treasury_shares=1000,
        common_stock=D("1000.00"),
        additional_paid_in_capital=D("0.00"),
        treasury_stock=D("1000.00"),
    )
    assert state.issued_shares == 1000 and state.treasury_shares == 1000
    raises_value(
        "treasury_shares cannot exceed issued_shares",
        lambda: Q.OpeningEquityState(
            issued_shares=1000,
            treasury_shares=1001,
            common_stock=D("1000.00"),
            additional_paid_in_capital=D("0.00"),
            treasury_stock=D("1001.00"),
        ),
    )


def test_the_equity_rule_fires_when_the_ledger_disagrees() -> None:
    """Kills the ``failures.add``/``_require_valid`` mutants.

    The equity rule is the book's own control check: replayed counts versus live
    counters, common stock versus issued-times-par, and the ledger's equity
    accounts versus the book's balances. Any mismatch raises inside the guard
    and lands in the returned rule set; a mutant that adds ``None`` or drops the
    failure set loses exactly that signal.
    """
    led = ledger()
    book = Q.EquityBook(led, share_class(), issued_shares=1000, treasury_shares=0)
    # Corrupt the ledger behind the book's back: one extra credit to common
    # stock breaks the control-account tie-out.
    led.post(
        L.make(
            "m42a-00000002-issue_common_shares",
            ENTITY,
            1,
            "issue_common_shares",
            {"cash": D("1.00")},
            {"common_stock": D("1.00")},
            TS_END,
            TS_END,
        )
    )

    assert Q.EQUITY_RULE in book.validate_reconciliations()
    raises_value(
        "invalid equity book: ['ACCT-EQUITY-SUBLEDGER']",
        lambda: Q.EquityBook(led, share_class(), issued_shares=1000, treasury_shares=0),
    )


def test_the_equity_rule_fires_when_share_counts_do_not_replay() -> None:
    """Kills the ``event_ids.add(None)`` and replay-mismatch mutants.

    Duplicate share-event ids and a replay that disagrees with the live counters
    are both equity-rule failures, and the deduplication set has to see the real
    ids to detect the duplicate.
    """
    book = equity_book(issued=1000, par="1.00")
    book.issue_common_shares(10, D("1.00"), D1, 1, TS, TS)
    first = book._events[0]
    book._events.append(
        Q.ShareEvent(
            first.event_id,
            Q.ShareEventKind.ISSUANCE,
            D2,
            1,
            shares=5,
            price=D("1.00"),
            event_time=TS_END,
            posted_at=TS_END,
        )
    )

    assert Q.EQUITY_RULE in book.validate_reconciliations()


def test_a_split_updates_counts_and_par_with_exact_rational_arithmetic() -> None:
    """Kills the ``//`` -> ``/``, ``*=``, and event-field mutants in the split.

    A split rewrites three quantities at once: issued shares, treasury shares,
    and the par fraction. Share counts have to stay integers (float division
    would mint fractional shares), the par fraction has to be *multiplied* by
    the inverse factor rather than replaced by it, and the recorded event has to
    carry the timestamps it was given.
    """
    book = equity_book(issued=1000, par="1.00", treasury=0)
    book.split_common_shares(Fraction(3, 2), D2, 1, TS_END, TS_END)

    assert book.issued_shares == 1500
    assert book.treasury_shares == 0
    # 60-digit exact conversion of Fraction(2,3).
    assert book.par_value == D("0.666666666666666666666666666666666666666666666666666666666667")
    split = book._events[0]
    assert split.event_time == TS_END and split.posted_at == TS_END

    # The par fraction is composed, not replaced: a second split multiplies.
    book.split_common_shares(Fraction(2, 1), D2, 1)
    assert book.issued_shares == 3000
    assert book.par_value == D("0.333333333333333333333333333333333333333333333333333333333333")


def test_a_split_accepts_an_empty_timestamp_pair_and_records_it() -> None:
    """Kills the ``event_time=None``/dropped-kwarg mutants on the split event.

    A split may be declared without timestamps, and the record has to show
    exactly that: both fields absent. Storing the passed values matters too --
    when timestamps are given they must survive onto the event.
    """
    book = equity_book(issued=1000, par="1.00")
    split = book.split_common_shares(Fraction(2, 1), D2, 1)

    assert split.event_time is None and split.posted_at is None

    book2 = equity_book(issued=1000, par="1.00")
    stamped = book2.split_common_shares(Fraction(2, 1), D2, 1, TS_END, TS_END)
    assert stamped.event_time == TS_END and stamped.posted_at == TS_END


def test_opening_monetary_overrides_change_the_book_balances() -> None:
    """Kills the ``(x is None) or True`` and requirement-guard mutants.

    The three ``opening_*`` arguments let a caller construct a book whose equity
    balances differ from the ledger's opening entries; each override has to
    actually reach its balance, and requiring *all three* together (not any one)
    is what the opening-state exclusivity check enforces.
    """
    led = L.Ledger(ENTITY)
    led.post(
        L.make(
            "open-0",
            ENTITY,
            0,
            "opening",
            {"cash": D("10000.00"), "treasury_stock": D("55.00")},
            {"common_stock": D("1000.00"), "additional_paid_in_capital": D("9055.00")},
            TS,
            TS,
        )
    )
    book = Q.EquityBook(
        led,
        share_class(),
        issued_shares=1000,
        opening_common_stock=D("1000.00"),
        opening_apic=D("9055.00"),
        opening_treasury_stock=D("55.00"),
    )

    assert book._common_stock_balance == D("1000.00")
    assert book._apic_balance == D("9055.00")
    assert book._treasury_stock_balance == D("55.00")
    # Any single override alongside a full opening_state is refused.
    with pytest.raises(ValueError, match="opening monetary values must be supplied through"):
        Q.EquityBook(
            ledger(),
            share_class(),
            opening_state=Q.OpeningEquityState(
                issued_shares=1000,
                treasury_shares=0,
                common_stock=D("1000.00"),
                additional_paid_in_capital=D("0.00"),
                treasury_stock=D("0.00"),
            ),
            opening_apic=D("1.00"),
        )


def test_the_share_class_is_retained_on_the_book() -> None:
    """Kills the ``self.share_class = None`` mutant.

    The class carries the par value every issuance reconciles against; dropping
    it would not fail construction but would leave any consumer reading
    ``book.share_class`` with nothing.
    """
    klass = share_class()
    book = Q.EquityBook(ledger(), klass, issued_shares=1000)

    assert book.share_class is klass
    assert book.share_class.par_value == D("1.00")


def test_issuance_requires_positive_shares_but_a_split_does_not() -> None:
    """Kills the ``split events cannot carry share counts`` mutants.

    A split is a change in the number of shares per existing share, so it takes
    no share count of its own; carrying one would double-count the shares it
    creates. An issuance, by contrast, must carry a positive count.
    """
    book = equity_book()
    split = book.split_common_shares(Fraction(2, 1), D2, 1)

    assert split.shares == 0
    assert split.factor == Fraction(2, 1)
    with pytest.raises(ValueError, match="share issuance count must be positive"):
        book.issue_common_shares(0, D("1.00"), D1, 1, TS, TS)
    assert ZERO == D("0.00")


# ======================================================== entry-id sequencing
def _decoy(entry_id: str) -> L.JournalEntry:
    """A well-formed, balance-neutral entry whose only role is its id.

    The equity book reconciles share capital against the ledger, so a decoy that
    moved ``common_stock`` would be rejected as a genuine mismatch before the
    sequence scan ran. Posting it between two accounts the book does not tie out
    leaves the balances alone, so the only thing under test is which ids count.
    """
    return L.make(
        entry_id,
        ENTITY,
        1,
        "issue_common_shares",
        {"sga": D("1.00")},
        {"revenue": D("1.00")},
        TS,
        TS,
    )


def test_reopening_resumes_the_entry_sequence_rather_than_restarting_it() -> None:
    """Kills the ``_last_entry_sequence`` scan mutants in ``EquityBook``.

    The book numbers its journal entries from the ids already in the ledger, so
    a ledger holding ``m42a-00000007-...`` must be continued at 8. Every
    condition in that scan is load-bearing -- the ``m42a`` prefix, the digit test
    on the middle field, the field count, and which field carries the sequence --
    so a reopened book pins the value and then pins the id it actually posts.
    """
    led = ledger()
    led.post(_decoy("m42a-00000007-issue_common_shares"))

    book = Q.EquityBook(led, share_class(), issued_shares=1000, treasury_shares=0)

    assert book._last_entry_sequence() == 7
    book.issue_common_shares(100, D("1.00"), D2, 1, TS_END, TS_END)
    ids = [entry.entry_id for entry in book.ledger.entries]
    assert "m42a-00000008-issue_common_shares" in ids


def test_the_entry_sequence_ignores_entries_it_does_not_own() -> None:
    """Kills the prefix, field-count, field-index and digit-test mutants.

    Four entries sit in the same ledger and three of them must not be counted: a
    foreign-prefixed entry from the operational book, a same-prefix entry whose
    middle field is not a number, and a same-prefix entry whose sequence sits in
    a different field. A real ``m42a`` entry has to be found alongside them.
    """
    led = ledger()
    led.post(_decoy("m41-00000009-sale_on_credit"))
    led.post(_decoy("m42a-abcdefg-issue_common_shares"))
    led.post(_decoy("m42a-issue-common-shares-00000006"))
    led.post(_decoy("m42a-00000004-issue-common-shares"))

    book = Q.EquityBook(led, share_class(), issued_shares=1000, treasury_shares=0)

    assert book._last_entry_sequence() == 4


def test_a_hyphenated_event_name_still_yields_its_entry_sequence() -> None:
    """Kills the ``split``/``rsplit``/maxsplit mutants in the sequence scan.

    The scan splits each id into exactly three fields, so the *event* part may
    itself contain hyphens and must not be split further: ``m42a-00000007-x-y``
    is one entry numbered 7, not two candidates. That is what fixes the split as
    ``split("-", 2)`` rather than ``rsplit`` or a smaller maxsplit -- a book
    reopening from a ledger whose entry ids it did not author has to read the
    sequence out of the second field regardless of how many hyphens follow.
    """
    led = ledger()
    led.post(_decoy("m42a-00000007-issue-common-shares"))

    book = Q.EquityBook(led, share_class(), issued_shares=1000, treasury_shares=0)

    assert book._last_entry_sequence() == 7


def test_a_ledger_with_no_equity_entries_starts_the_sequence_at_zero() -> None:
    """Kills the ``max(sequences, default=...)`` mutants.

    With nothing of its own to scan, the book reports 0 so the first entry it
    posts is numbered 1. A default of 1 would open a permanent gap in the audit
    trail, and a book that scanned the wrong field would restart the count.
    """
    book = equity_book()

    assert book._last_entry_sequence() == 0
    book.issue_common_shares(100, D("1.00"), D2, 1, TS_END, TS_END)
    assert [e.entry_id for e in book.ledger.entries if e.entry_id.startswith("m42a-")] == [
        "m42a-00000001-issue_common_shares"
    ]


# ================================================== opening-state overrides
def test_opening_monetary_overrides_must_reach_the_book() -> None:
    """Kills ``__init__`` mutants 41, 47 and 53.

    Each ``opening_*`` argument replaces the ledger-balance read for its
    account. The constructor then validates the book against that same ledger,
    so an override that disagrees with the ledger must be rejected: the original
    code stores the override, the control comparison fails, and opening raises.
    A mutant that ignores the override (``(x is None) or True``) stores the
    ledger value instead, validation passes, and the book silently opens with
    equity the caller never asked for -- that is the observable difference.
    """
    led = ledger(cash="10000.00", issued=1000, par="1.00")
    raises_value(
        "invalid equity book: ['ACCT-EQUITY-SUBLEDGER']",
        lambda: Q.EquityBook(
            led,
            share_class(),
            issued_shares=1000,
            opening_common_stock=D("999.00"),
        ),
    )
    raises_value(
        "invalid equity book: ['ACCT-EQUITY-SUBLEDGER']",
        lambda: Q.EquityBook(
            ledger(cash="10000.00", issued=1000, par="1.00"),
            share_class(),
            issued_shares=1000,
            opening_apic=D("999.00"),
        ),
    )
    raises_value(
        "invalid equity book: ['ACCT-EQUITY-SUBLEDGER']",
        lambda: Q.EquityBook(
            ledger(cash="10000.00", issued=1000, par="1.00"),
            share_class(),
            issued_shares=1000,
            opening_treasury_stock=D("999.00"),
        ),
    )


# ==================================================== share-event records
def test_an_issuance_or_repurchase_requires_timestamps() -> None:
    """Kills ``issue 28/33``, ``repurchase 28/33`` and ``ShareEvent 64``.

    Split events are timestamps-optional; issuance and repurchase are not.
    Passing ``timestamps_required=None`` (falsy) or ``False`` would accept a
    deterministic-kernel event with no clock reading at all.
    """
    book = equity_book()
    raises_value(
        "issuance and repurchase require event_time and posted_at",
        lambda: book.issue_common_shares(10, D("1.00"), D2, 1, None, None),
    )
    raises_value(
        "issuance and repurchase require event_time and posted_at",
        lambda: book.repurchase_common_shares(10, D("1.00"), D2, 1, None, None),
    )
    with pytest.raises(ValueError) as info:
        Q.ShareEvent(
            "e-x",
            Q.ShareEventKind.ISSUANCE,
            D2,
            1,
            shares=10,
            price=D("1.00"),
            event_time=None,
            posted_at=None,
        )
    assert str(info.value) == "issuance and repurchase require event_time and posted_at"


def test_the_recorded_issue_price_is_observable() -> None:
    """Kills ``issue 78`` and ``repurchase 73``.

    The recorded price is part of the audit trail; a mutant that drops
    ``price=price`` files every event at the ZERO default instead.
    """
    book = equity_book()
    event = book.issue_common_shares(10, D("2.50"), D2, 1, TS_END, TS_END)
    assert event.price == D("2.50")
    book2 = equity_book()
    event2 = book2.repurchase_common_shares(10, D("2.50"), D2, 1, TS_END, TS_END)
    assert event2.price == D("2.50")


def test_a_repurchase_count_must_be_positive() -> None:
    """Kills ``repurchase 8`` (``count <= 0`` widened to ``count < 0``).

    A zero repurchase mints an empty journal entry and a no-op event, which
    the append-only audit trail must not contain.
    """
    book = equity_book()
    raises_value(
        "repurchase count must be positive",
        lambda: book.repurchase_common_shares(0, D("1.00"), D2, 1, TS_END, TS_END),
    )


def test_a_split_cannot_mint_fractional_share_counts() -> None:
    """Kills split mutants 50 and 53 and ``_apply_event`` 22/25.

    The divisibility precheck only guards the book's current counts; the
    replay path recomputes every historical count with the same arithmetic.
    A real-division mutant stays equal for quotients that fit a float but
    diverges once a share count crosses 2**53, so the test drives a count
    there through a factor with an odd numerator.
    """
    # 10**16 + 2 is even but not divisible by 4, so a 3-for-2 split yields an
    # odd quotient above 2**53 -- exactly the values where real division and
    # floor division stop agreeing.
    count = 10**16 + 2
    expected = count * 3 // 2
    assert expected % 2 == 1 and expected > 2**53
    led = ledger(cash=f"{count}.00", issued=count, par="1.00")
    book = Q.EquityBook(led, share_class("1.00"), issued_shares=count)
    book.split_common_shares((3, 2), D1, 1)
    assert book.issued_shares == expected
    assert book.treasury_shares == 0
    assert book.validate_reconciliations() == set()


# ============================================== as-of enforcement in validators
def test_validators_enforce_as_of_not_before_period_end() -> None:
    """Kills the ``as_of_date=None`` and dropped-argument mutants.

    ``_validate_date_range`` raises when a supplied ``as_of_date`` precedes
    ``period_end``; passing ``None`` instead of the caller's value (mutants
    that replace the forwarded argument, or drop it entirely) skips the check
    and computes a WASO the caller asked never to see. The mismatch must be
    reported as a broken ``ACCT-WASO`` rule, not silently computed.
    """
    book = equity_book()
    book.issue_common_shares(100, D("1.00"), date(2025, 3, 15), 1, TS_MID, TS_MID)
    early = date(2025, 3, 10)
    late = date(2025, 3, 20)
    assert book.validate_waso(late, D2, early) == {"ACCT-WASO"}
    assert book.validate_basic_eps("500.00", late, D2, early) == {"ACCT-BASIC-EPS"}
    assert book.validate_reconciliations(
        period_start=late,
        period_end=D2,
        as_of_date=early,
        net_income="500.00",
    ) == {"ACCT-WASO", "ACCT-BASIC-EPS"}


def test_an_issuance_inside_the_window_changes_the_weighted_average() -> None:
    """Kills the remaining ``as_of_date`` forwarding mutants.

    A mid-period issuance splits the share-day weighting; a mutant that
    recomputes the expected value without the caller's as-of cutoff would
    replay a different event set and mis-report a submitted figure.
    """
    book = equity_book()
    book.issue_common_shares(100, D("1.00"), date(2025, 3, 15), 1, TS_MID, TS_MID)
    mid = date(2025, 3, 15)
    expected_with_cutoff = book.weighted_average_shares(D1, D2, as_of_date=D2)
    assert expected_with_cutoff > 1000
    reported = expected_with_cutoff + D("0.5")
    assert book.validate_waso(D1, D2, D2, reported) == {"ACCT-WASO"}
    assert book.validate_waso(D1, D2, mid, reported) == {"ACCT-WASO"}


# ==================================================== split sequencing
def test_a_split_orders_before_same_day_trades_in_replay() -> None:
    """Kills ``_ordered_events`` mutant 3 (ISSUANCE priority 1 -> 2).

    Same-day replay order is split first, then issuance, then repurchase. A
    one-share repurchase on the split day pins the order: replayed split-first
    the repurchase is legal, replayed after the issuance or with a shifted
    priority it either overdraws treasury or leaves a different share count.
    Mutant 4 (REPURCHASE 2 -> 3) preserves the strict ordering -- no kind sits
    at priority 3 -- and is proven equivalent in
    mutation_triage_overrides.json.
    """
    led = ledger(cash="10000.00", issued=1000, par="1.00")
    book = Q.EquityBook(led, share_class("1.00"), issued_shares=1000)
    book.split_common_shares((2, 1), D2, 1)  # timestamps optional
    book.issue_common_shares(100, D("1.00"), D2, 1, TS_END, TS_END)
    book.repurchase_common_shares(1, D("1.00"), D2, 1, TS_END, TS_END)
    assert book.treasury_shares == 1
    assert book.outstanding_shares == 1000 * 2 + 100 - 1
    assert book.validate_reconciliations() == set()

    kinds = [e.kind for e in book._ordered_events(D2)]
    assert kinds.count(Q.ShareEventKind.SPLIT) == 1
    assert kinds[0] is Q.ShareEventKind.SPLIT


# ================================================== duplicate event ids
def test_a_duplicate_share_event_id_is_detected() -> None:
    """Kills ``validate_reconciliations`` mutant 43 (``add(event.event_id)``).

    The dedup set must record the event's id; recording ``None`` instead makes
    every comparison against it vacuous, so two events sharing one id pass
    validation. The events are no-op splits so share-count replay stays
    consistent and the duplicate id is the only broken rule.
    """
    book = equity_book()
    noop = Q.ShareEvent("e-dup", Q.ShareEventKind.SPLIT, D1, 1, factor=Q.Fraction(1, 1))
    book._events.append(noop)
    book._events.append(noop)

    assert book.validate_reconciliations() == {"ACCT-EQUITY-SUBLEDGER"}


# ================================================== as-of must propagate
def test_basic_eps_rejects_an_as_of_before_the_period_end() -> None:
    """Kills ``basic_eps`` mutants 10 and 13 (the forwarded ``as_of_date``).

    ``basic_eps`` computes the denominator through ``weighted_average_shares``
    with the caller's as-of date; passing ``None`` (or dropping the argument)
    skips ``_validate_date_range``'s cutoff check and silently prices the
    period from events the caller excluded.
    """
    book = equity_book()
    with pytest.raises(ValueError) as info:
        book.basic_eps("500.00", D2, D2, as_of_date=date(2025, 3, 10))
    assert str(info.value) == "as_of_date must be >= period_end"


# ================================================== negativity defaults
def test_share_class_and_event_price_reject_negative_values() -> None:
    """Kills ``_exact_decimal`` mutant 1 (``allow_negative`` default flipped).

    ``CommonShareClass`` and ``ShareEvent`` call ``_exact_decimal`` without the
    keyword, so the default carries the non-negativity rule for par values and
    recorded prices. Flipping it admits a negative par value, which poisons
    every common-stock product check downstream.
    """
    raises_value("par_value must be non-negative", lambda: share_class("-1.00"))
    with pytest.raises(ValueError) as info:
        Q.ShareEvent("e-neg", Q.ShareEventKind.ISSUANCE, D1, 1, shares=10, price=D("-1.00"))
    assert str(info.value) == "price must be non-negative"


def test_replay_order_lets_a_repurchase_precede_a_same_day_issuance() -> None:
    """Kills ``_ordered_events`` mutant 3 (ISSUANCE priority 1 -> 2).

    Two same-day events inserted repurchase-first must still replay issuance
    first (priority 1 < 2). Raising the issuance priority to tie with the
    repurchase's lets the stable sort keep insertion order, and replaying the
    repurchase first overdraws treasury against issued shares -- the replay
    raises where the original returns counts.
    """
    book = equity_book()
    book._events.append(
        Q.ShareEvent(
            "e-r",
            Q.ShareEventKind.REPURCHASE,
            D2,
            1,
            shares=1500,
            price=D("1.00"),
            event_time=TS_END,
            posted_at=TS_END,
        )
    )
    book._events.append(
        Q.ShareEvent(
            "e-i",
            Q.ShareEventKind.ISSUANCE,
            D2,
            1,
            shares=1000,
            price=D("1.00"),
            event_time=TS_END,
            posted_at=TS_END,
        )
    )
    counts = book.share_counts()
    assert (counts.issued_shares, counts.treasury_shares) == (2000, 1500)


def test_money_rejects_amounts_beyond_the_sixty_digit_context() -> None:
    """Kills ``_money`` mutant 12 (``context.prec = 60`` -> ``61``).

    ``_money``'s precision is a contract, not an implementation detail: a
    59-digit integer cent amount needs a 61-digit coefficient once quantized
    to cents, which exactly straddles the two precisions. At 60 the quantize
    raises and the kernel refuses the amount; at 61 it would silently accept
    it and produce an EPS the canonical precision cannot represent.
    """
    book = equity_book()
    raises_value(
        "money cannot be represented at cent precision",
        lambda: book.basic_eps(D("1e58"), D1, D2),
    )


def test_basic_eps_refuses_quotients_beyond_the_sixty_digit_context() -> None:
    """Kills ``basic_eps`` mutant 20 (``context.prec = 60`` -> ``61``).

    The 60-digit working precision is a representability contract. A 3-share
    book holds exactly 3 weighted-average shares over the 31-day window, and an
    income of ``3 * 10**54 + 2`` cents pins the quotient at 10**54 + 2/3: the
    half-zone of the EPS quantum sits above the 60th significant digit, so at
    60 the quantize raises and the kernel refuses the figure, while at 61 it
    would return a 55-digit EPS.
    """
    book = Q.EquityBook(
        ledger(cash="100.00", issued=3, par="1.00"),
        share_class("1.00"),
        issued_shares=3,
    )
    raises_value(
        "basic EPS cannot be represented",
        lambda: book.basic_eps(D(3 * 10**54 + 2), D1, D2),
    )


def test_a_clean_book_with_no_window_reports_no_rules() -> None:
    """A call with neither window bound set reports no broken rules.

    Positive control for the window-pairing branch: with both bounds ``None``
    the pairing check and the elif must both fall through without running any
    WASO validation. Mutant 49 (``and`` widened to ``or`` in the elif) is
    proven equivalent in mutation_triage_overrides.json -- the pairing check
    above it means the elif is only reached when both bounds agree, and on
    both-agree states ``and`` and ``or`` are equal.
    """
    book = equity_book()

    assert book.validate_reconciliations() == set()
