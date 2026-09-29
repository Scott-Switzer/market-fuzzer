"""M5.3 killing tests for :mod:`fwf_kernel.ledger`.

Every test here exists because a specific mutant of ``ledger.py`` survived the
canonical FWF kernel suite. The canonical suite is byte-pinned to
``financial-system-core`` and must not be edited, so the tests that close its
gaps live here instead; each test names the mutants it kills in its docstring,
and ``tests/m5/mutation_triage.json`` records the survivors it does *not* kill
together with the proof that they are unobservable.

The survivors clustered on three things the canonical suite never exercised:

* :meth:`Ledger.close` -- the canonical suite never closed a period that had
  profit-and-loss activity, so the entire close entry was unobserved: which
  accounts it sweeps, which side of the entry each one lands on, its period
  scoping, its negative-balance branch and its retained-earnings leg.
* :func:`income_statement` and :func:`balance_sheet` -- the canonical suite
  never read a derived statement, so neither their arithmetic nor the *names* of
  the keys they publish were pinned. Those names are part of the report
  contract; a statement that renames ``gross_profit`` breaks every consumer.
* the diagnostic text of the :class:`Line` and :class:`JournalEntry` guards --
  the canonical suite asserted only the exception type, so a guard that stopped
  naming the offending value was invisible.

Conventions used throughout:

* ``nets(entry)`` reports, per account, debits minus credits. Summed over an
  entry that total is zero for any entry the kernel accepts, which makes it the
  cheapest possible check that a close entry is well formed.
* ``led.balances()`` reports each balance in the account's *own normal
  direction*, so a positive revenue balance is revenue the company has earned.
  Where a test needs to say "retained earnings rose", it says that in those
  terms rather than in raw debit/credit.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from fwf_kernel import ledger as L

D = Decimal
ENTITY = "ENT-KILL-LEDGER"
TS = "2025-03-01T00:00:00Z"

#: Every key :func:`income_statement` publishes, in order. The order and the
#: spelling are both contract: downstream consumers read them by name.
IS_KEYS = (
    "revenue",
    "cogs",
    "gross_profit",
    "sga",
    "depreciation",
    "operating_income",
    "interest",
    "pretax_income",
    "tax_expense",
    "net_income",
)


def book() -> L.Ledger:
    return L.Ledger(ENTITY)


def entry(
    entry_id: str,
    period: int,
    event: str,
    dr: dict[str, str] | None = None,
    cr: dict[str, str] | None = None,
) -> L.JournalEntry:
    return L.make(entry_id, ENTITY, period, event, dr or {}, cr or {}, TS, TS)


def post(led: L.Ledger, entry_id: str, period: int, name: str, amount: str) -> None:
    led.post(L.event(entry_id, ENTITY, period, name, amount, TS, TS))


def nets(entry_obj: L.JournalEntry) -> dict[str, Decimal]:
    """Debits minus credits per account, for one journal entry."""
    out: dict[str, Decimal] = {}
    for line in entry_obj.lines:
        out[line.account] = out.get(line.account, D(0)) + line.debit - line.credit
    return out


def close_entry(led: L.Ledger) -> L.JournalEntry:
    closes = [e for e in led.entries if e.event == "close"]
    assert closes, "no close entry was posted"
    return closes[-1]


def raises_value(expected: str, call: Callable[[], object]) -> None:
    """Assert ``call`` raises ``ValueError`` whose message is exactly ``expected``.

    Asserting only the exception *type* leaves the diagnostic unconstrained,
    which is how ``raise ValueError("period must be >= 0")`` can quietly become
    ``raise ValueError(None)`` with every test still green. Asserting only a
    *substring* is not enough either: wrapping the literal -- ``"XXperiod must be
    >= 0XX"`` -- still contains the original text, so the message has to be pinned
    in full. These are the messages an operator greps for and the only thing that
    says which guard fired, so they are part of the contract rather than an
    implementation detail.
    """
    with pytest.raises(ValueError) as info:
        call()
    assert str(info.value) == expected, f"message {str(info.value)!r} is not {expected!r}"


def raises_containing(fragment: str, call: Callable[[], object]) -> None:
    """Assert ``call`` raises an exception whose message names ``fragment``.

    Used where the exception is a ``KeyError``: ``str(KeyError(msg))`` is
    ``repr(msg)``, so an exact comparison would pin the quoting as well as the
    text, and the mutants that matter here drop the message entirely rather than
    rewrapping it.
    """
    with pytest.raises(KeyError) as info:
        call()
    assert fragment in str(info.value), f"message {str(info.value)!r} omits {fragment!r}"


# ============================================================ Ledger.close
def test_close_sweeps_revenue_and_expense_into_retained_earnings() -> None:
    """Kills ``close`` mutants 27, 28, 29 (the side chosen per account normal).

    Closing must reverse each P&L account against the account's own normal
    direction: a credit-normal revenue balance is closed with a *debit*, a
    debit-normal expense balance with a *credit*. Mutants 27 and 28 pin the
    debit-normal branch shut and mutant 29 inverts the test, so all three put
    revenue and expense on the wrong side of the entry.
    """
    led = book()
    post(led, "sale-1", 1, "sale_on_credit", "1000.00")
    post(led, "sga-1", 1, "pay_sga", "250.00")

    led.close(1, TS)

    close = nets(close_entry(led))
    assert close["revenue"] == D("1000.00"), "revenue is credit-normal, so closing debits it"
    assert close["sga"] == D("-250.00"), "expense is debit-normal, so closing credits it"
    assert close["retained_earnings"] == D("-750.00")
    assert sum(close.values(), D(0)) == D(0)

    after = led.balances(only=1)
    assert after["revenue"] == D(0)
    assert after["sga"] == D(0)
    assert after["retained_earnings"] == D("750.00")
    assert led.closed_periods == {1}


def test_close_sweeps_revenue_past_a_zero_balance_expense() -> None:
    """Kills ``close`` mutant 25 (the ``v == 0`` guard's ``continue`` -> ``break``).

    ``CHART`` is iterated in declaration order: ``revenue`` precedes ``cogs``,
    which precedes ``sga``. A period with revenue and SG&A but no COGS therefore
    meets a zero balance *between* two accounts that do have one, and a ``break``
    there abandons the rest of the sweep. This is the shape of the bug that
    ``continue`` exists to prevent, and it silently drops revenue.
    """
    led = book()
    post(led, "sale-1", 1, "sale_on_credit", "1000.00")
    post(led, "sga-1", 1, "pay_sga", "250.00")
    assert led.balances(only=1)["cogs"] == D(0)

    led.close(1, TS)

    close = nets(close_entry(led))
    assert close["revenue"] == D("1000.00")
    assert close["sga"] == D("-250.00")
    assert led.balances(only=1)["retained_earnings"] == D("750.00")


def test_close_leaves_balance_sheet_accounts_untouched() -> None:
    """Kills ``close`` mutants 17 and 22.

    Mutant 17 weakens ``and`` to ``or`` in the sweep filter, which makes every
    balance-sheet account a candidate for the sweep and closes the balance sheet
    into retained earnings. Mutant 22 turns the filter's ``continue`` into a
    ``break``, so the *first* non-P&L account in ``CHART`` order -- ``cash`` --
    ends the sweep before it ever reaches revenue. Both are caught here by a
    period that has cash, AR and revenue in it.
    """
    led = book()
    post(led, "sale-1", 1, "sale_on_credit", "1000.00")
    post(led, "collect-1", 1, "collect_ar", "400.00")
    assert led.balances(only=1)["cash"] == D("400.00")
    assert led.balances(only=1)["ar"] == D("600.00")

    led.close(1, TS)

    close = nets(close_entry(led))
    assert "cash" not in close, "cash must not be swept into retained earnings"
    assert "ar" not in close, "AR must not be swept into retained earnings"

    after = led.balances(only=1)
    assert after["cash"] == D("400.00")
    assert after["ar"] == D("600.00")
    assert after["revenue"] == D(0)
    assert after["retained_earnings"] == D("1000.00")


def test_close_sweeps_dividends() -> None:
    """Kills ``close`` mutants 20 and 21 (the ``"dividends"`` literal).

    Dividends are equity rather than P&L, so the sweep reaches them through the
    ``a != "dividends"`` clause rather than through the account's kind. Drop or
    misspell that literal and declared dividends are left stranded in the period,
    overstating retained earnings by exactly the dividend.
    """
    led = book()
    post(led, "div-1", 1, "pay_dividend", "300.00")
    post(led, "sale-1", 1, "sale_on_credit", "100.00")

    led.close(1, TS)

    close = nets(close_entry(led))
    # Dividends are debit-normal, so closing credits them; revenue is
    # credit-normal, so closing debits it. 300 against 100 is a net loss of 200.
    assert close["dividends"] == D("-300.00")
    assert close["revenue"] == D("100.00")
    assert close["retained_earnings"] == D("200.00")
    assert sum(close.values(), D(0)) == D(0)
    assert led.balances(only=1)["dividends"] == D(0)
    assert led.balances(only=1)["retained_earnings"] == D("-200.00")


def test_close_handles_a_contra_revenue_balance() -> None:
    """Kills ``close`` mutants 32, 33, 34 and 35 (the negative-balance flip).

    A revenue account carrying a *negative* balance -- a contra-revenue position
    such as cumulative sales returns -- closes on the opposite side from a normal
    positive revenue balance. The canonical suite only ever closed positive P&L,
    so this branch never executed; mutant 32 additionally dereferences ``None``
    where the side dict should be.
    """
    led = book()
    post(led, "sale-1", 1, "sale_on_credit", "500.00")
    # Sales returns are debits to revenue: 200 then 400 against 500 of sales.
    led.post(entry("return-a", 1, "sales_return", {"revenue": "200.00"}, {"ar": "200.00"}))
    led.post(entry("return-b", 1, "sales_return", {"revenue": "400.00"}, {"ar": "400.00"}))
    assert led.balances(only=1)["revenue"] == D("-100.00"), "precondition: contra revenue"

    led.close(1, TS)

    close = nets(close_entry(led))
    # A negative revenue balance is closed with a credit, reversing the debit.
    assert close["revenue"] == D("-100.00")
    assert close["retained_earnings"] == D("100.00")
    assert sum(close.values(), D(0)) == D(0)
    assert led.balances(only=1)["revenue"] == D(0)
    assert led.balances(only=1)["retained_earnings"] == D("-100.00")


def test_close_handles_a_contra_expense_balance() -> None:
    """Kills ``close`` mutant 33 (the negative-balance flip, debit-normal arm).

    The flip is ``side = dr if side is cr else cr``, so which arm fires depends
    on the side the account *started* on. A debit-normal expense starts on the
    credit side, and only a *negative* balance reaches the other arm -- a partial
    reversal leaves a positive net balance and never gets there. A credit-normal
    contra-revenue position, which :func:`test_close_handles_a_contra_revenue_balance`
    covers, starts on the debit side, so it exercises the other arm. Both are
    needed or the flip is half-tested.
    """
    led = book()
    post(led, "sga-1", 1, "pay_sga", "500.00")
    # A vendor credit memo larger than the expense it reverses, so the period
    # ends with SG&A carrying a credit balance: a contra-expense position.
    led.post(entry("memo-1", 1, "expense_reversal", {"cash": "800.00"}, {"sga": "800.00"}))
    assert led.balances(only=1)["sga"] == D("-300.00"), "precondition: contra expense"

    led.close(1, TS)

    close = nets(close_entry(led))
    # The negative balance reverses, so the sweep *debits* SG&A. With no revenue
    # in the period, a net credit in an expense account is net income, so
    # retained earnings takes the matching credit: the company earned 300.
    assert close["sga"] == D("300.00")
    assert close["retained_earnings"] == D("-300.00")
    assert sum(close.values(), D(0)) == D(0)
    assert led.balances(only=1)["sga"] == D(0)
    assert led.balances(only=1)["retained_earnings"] == D("300.00")
    assert led.balances(only=1)["cash"] == D("300.00")


def test_close_handles_a_sub_unit_balance() -> None:
    """Kills ``close`` mutant 31 (``v < 0`` widened to ``v < 1``).

    Amounts are cent-exact, so a period result of 50 cents is a small *positive*
    balance and must not be classified as a negative one. Any threshold between
    zero and one currency unit gets it backwards, which would credit revenue and
    produce a close entry that does not net to zero.
    """
    led = book()
    post(led, "sale-1", 1, "sale_on_credit", "0.50")

    led.close(1, TS)

    close = nets(close_entry(led))
    assert close["revenue"] == D("0.50")
    assert close["retained_earnings"] == D("-0.50")
    assert sum(close.values(), D(0)) == D(0)
    assert led.balances(only=1)["retained_earnings"] == D("0.50")


def test_close_ignores_other_periods() -> None:
    """Kills ``close`` mutants 7 and 9 (the ``only=period`` scope dropped).

    A close considers only the period being closed. Sweeping every period
    instead would pull earlier, still-open periods' results into this close and
    double-count them into retained earnings.
    """
    led = book()
    post(led, "sale-1", 1, "sale_on_credit", "100.00")
    post(led, "sale-2", 2, "sale_on_credit", "250.00")

    led.close(2, TS)

    assert nets(close_entry(led))["retained_earnings"] == D("-250.00")
    assert led.balances(only=2)["retained_earnings"] == D("250.00")
    # Period 1 keeps its revenue until it is closed in its own right.
    assert led.balances(only=1)["revenue"] == D("100.00")
    assert led.balances(only=1)["retained_earnings"] == D(0)


def test_close_is_idempotent_over_an_already_closed_period() -> None:
    """Kills ``close`` mutants 10, 12 and 13 (the ``exclude={"close"}`` guard).

    Re-closing a period is reachable -- :meth:`close` records the period in
    ``closed_periods`` but never consults it -- and the ``exclude`` argument is
    what makes that state *loud*. The first close entry is a reversal of the
    P&L accounts, so excluding it leaves the second close looking at the
    original revenue and expense and rebuilding an identical entry under an
    already-used id: the duplicate guard fires. Include it instead and the
    balances net to zero, no entry is posted, and the double close disappears
    without a word. A silent no-op on an already-closed period is the worse
    failure, so the diagnostic is the behaviour worth pinning.
    """
    led = book()
    post(led, "sale-1", 1, "sale_on_credit", "1000.00")
    post(led, "sga-1", 1, "pay_sga", "250.00")

    led.close(1, TS)
    first = nets(close_entry(led))
    assert len([e for e in led.entries if e.event == "close"]) == 1

    raises_value("duplicate entry close-1", lambda: led.close(1, TS))
    closes = [e for e in led.entries if e.event == "close"]
    assert len(closes) == 1, f"re-closing must not post a second entry, got {len(closes)}"
    assert nets(closes[0]) == first
    assert led.closed_periods == {1}


def test_close_posts_an_entry_with_a_single_swept_account() -> None:
    """A one-sided period still produces a balanced, two-sided close entry."""
    led = book()
    post(led, "sale-1", 1, "sale_on_credit", "100.00")

    led.close(1, TS)

    close = nets(close_entry(led))
    assert close == {"revenue": D("100.00"), "retained_earnings": D("-100.00")}
    assert led.balances(only=1)["retained_earnings"] == D("100.00")


def test_close_reports_a_loss_as_a_debit_to_retained_earnings() -> None:
    """Kills ``close`` mutants 17, 30 and 64 in their loss-reporting shape.

    With no revenue, the sweep is one debit-normal expense and the period result
    is negative, so retained earnings takes a *debit*. Mutant 30 classifies the
    balance wrongly, mutant 17 sweeps the cash that funded the expense, and
    mutant 64 requires both sides of the close to be non-empty and so skips the
    entry altogether.
    """
    led = book()
    post(led, "sga-1", 1, "pay_sga", "75.00")

    led.close(1, TS)

    close = nets(close_entry(led))
    assert close["sga"] == D("-75.00")
    assert close["retained_earnings"] == D("75.00")
    assert sum(close.values(), D(0)) == D(0)
    assert led.balances(only=1)["retained_earnings"] == D("-75.00")


def test_close_of_a_period_with_no_activity_posts_nothing() -> None:
    """A period with no P&L must not post an empty close entry."""
    led = book()
    post(led, "sale-1", 1, "sale_on_credit", "100.00")

    led.close(2, TS)

    assert not [e for e in led.entries if e.event == "close"]
    assert led.closed_periods == {2}


def test_close_rejects_a_negative_period() -> None:
    """Kills ``close`` mutant 4 (the ``period must be >= 0`` diagnostic).

    :meth:`close` takes a bare ``int``, so -- unlike :meth:`post` -- this guard is
    genuinely reachable, and it is the only thing standing between a caller and a
    negative period in ``closed_periods``.
    """
    led = book()
    raises_value("period must be >= 0", lambda: led.close(-1, TS))
    assert led.closed_periods == set()


def test_close_writes_into_the_side_it_selected() -> None:
    """Kills ``close`` mutant 38 (``side[a]`` keyed on ``None``).

    The selected side dict is written under the account's own name. Keying the
    write on ``None`` instead loses the account from the close entry entirely,
    which for a balance-checked entry means the period silently fails to close
    with an unkeyed lookup error instead.
    """
    led = book()
    post(led, "sale-1", 1, "sale_on_credit", "100.00")

    led.close(1, TS)

    assert "revenue" in nets(close_entry(led))


# ================================================================ Ledger.post
def test_post_rejects_an_entry_for_another_entity() -> None:
    """Kills ``post`` mutants 7, 8 and 9 (the ``another entity`` diagnostic)."""
    led = book()
    stray = entry_for("ENT-OTHER")
    raises_value("entry belongs to another entity", lambda: led.post(stray))
    assert led.entries == []


def entry_for(entity: str) -> L.JournalEntry:
    return L.make("x", entity, 1, "sale_on_credit", {"ar": "1.00"}, {"revenue": "1.00"}, TS, TS)


def test_post_rejects_duplicate_entry_ids() -> None:
    """Kills ``post`` mutant 12 (the ``duplicate`` diagnostic).

    Entry ids are the ledger's primary key: a repeat would double every account
    the entry touches and break the trial balance downstream.
    """
    led = book()
    led.post(entry("dup", 1, "sale_on_credit", {"ar": "100.00"}, {"revenue": "100.00"}))

    raises_value(
        "duplicate entry dup",
        lambda: led.post(entry("dup", 1, "sale_on_credit", {"ar": "100.00"}, {"revenue": "100.00"})),
    )
    assert [e.entry_id for e in led.entries] == ["dup"]


def test_post_rejects_a_second_entry_into_a_closed_period() -> None:
    """Kills ``post`` mutant 14 (the ``closed`` diagnostic).

    Once a period is closed its result is in retained earnings; a further posting
    would sit in the P&L accounts where no close will ever find it again.
    """
    led = book()
    post(led, "sale-1", 1, "sale_on_credit", "100.00")
    led.close(1, TS)

    raises_value(
        "period 1 is closed; post an adjustment in an open period",
        lambda: led.post(entry("adj-1", 1, "pay_sga", {"sga": "10.00"}, {"cash": "10.00"})),
    )
    assert [e.entry_id for e in led.entries] == ["sale-1", "close-1"]


def test_post_rejects_backdated_entries() -> None:
    """Kills ``post`` mutants 17, 22, 23 and 24.

    An ordinary entry may not be posted into a period earlier than the latest
    posted one. Mutant 17 inverts the event test so the guard fires for opening
    entries and never for ordinary ones, and 18/19 misspell the ``"opening"``
    literal so even that carve-out stops working; 22-24 discard the diagnostic.
    """
    led = book()
    post(led, "sale-1", 1, "sale_on_credit", "100.00")
    post(led, "sale-2", 2, "sale_on_credit", "100.00")

    raises_value(
        "backdated posting into an earlier period is not allowed",
        lambda: led.post(entry("late-1", 1, "pay_sga", {"sga": "10.00"}, {"cash": "10.00"})),
    )
    assert [e.entry_id for e in led.entries] == ["sale-1", "sale-2"]


def test_post_allows_an_opening_entry_in_an_earlier_period() -> None:
    """Kills ``post`` mutants 18 and 19 (the ``"opening"`` literal).

    The backdating guard has exactly one carve-out, and it is keyed on the event
    name: an opening balance entry is posted into period 0 by construction, so it
    is routinely backdated relative to the periods already loaded. Misspelling
    the literal closes that carve-out and the whole kernel becomes unloadable.
    """
    led = book()
    post(led, "sale-1", 1, "sale_on_credit", "100.00")

    led.post(entry("opening-1", 0, "opening", {"cash": "500.00"}, {"common_stock": "500.00"}))

    assert [e.entry_id for e in led.entries] == ["sale-1", "opening-1"]


# =================================================================== Line
def test_line_rejects_an_unknown_account() -> None:
    """Kills ``Line.__post_init__`` mutant 2 (the ``unknown account`` diagnostic).

    ``CHART`` is the only source of truth for account identity; a typo that
    reached the ledger would produce a balance sheet that silently omits the
    account rather than failing.
    """
    raises_containing("unknown account", lambda: L.Line("not_an_account", debit=D("1.00")))


def test_line_rejects_a_negative_debit() -> None:
    """Kills ``Line.__post_init__`` mutants 3 and 10.

    Mutant 3 weakens the second ``or`` to ``and``, so a negative credit on an
    otherwise well-formed line stops being caught; mutant 10 discards the
    diagnostic. A negative debit would invert the sign of the account's balance
    in the normal direction and quietly flip the statement it appears in.
    """
    raises_value(
        "a line is either a non-negative debit or a non-negative credit",
        lambda: L.Line("cash", debit=D("-1.00")),
    )


def test_line_rejects_a_negative_credit() -> None:
    """Kills ``Line.__post_init__`` mutants 4 and 10 (second disjunct of the guard).

    Mutant 4 rewrites the guard as ``debit < 0 and credit < 0 or (both)``, which
    only fires when *both* sides are negative -- the case the trailing disjunct
    already covers -- and so lets a lone negative credit through.
    """
    raises_value(
        "a line is either a non-negative debit or a non-negative credit",
        lambda: L.Line("cash", credit=D("-1.00")),
    )


def test_line_rejects_both_sides_at_once() -> None:
    """Kills ``Line.__post_init__`` mutants 4 and 12.

    A line is either a debit or a credit, never both: a line carrying both is a
    netting artefact that would let a single entry move an account in a direction
    neither of its two legs describes.
    """
    raises_value(
        "a line is either a non-negative debit or a non-negative credit",
        lambda: L.Line("cash", debit=D("1.00"), credit=D("1.00")),
    )


# =========================================================== JournalEntry
def test_entry_rejects_an_unbalanced_set_of_lines() -> None:
    """Kills ``JournalEntry.__post_init__`` mutants 8, 13 and 18.

    Every journal entry must net to zero. The three mutants here cover the two
    ``sum(..., ZERO)`` start values and the diagnostic, and the unbalanced entry
    below is the single most consequential thing the class prevents: an entry
    that does not net to zero is a broken trial balance that no later stage can
    detect.
    """
    raises_value(
        "bad: debits 100.00 != credits 0 (or empty entry)",
        lambda: L.make("bad", ENTITY, 1, "sale_on_credit", {"ar": "100.00"}, {}, TS, TS),
    )
    raises_value(
        "bad: debits 100.00 != credits 60.00 (or empty entry)",
        lambda: L.make("bad", ENTITY, 1, "sale_on_credit", {"ar": "100.00"}, {"revenue": "60.00"}, TS, TS),
    )


def test_entry_rejects_an_empty_line_set() -> None:
    """The ``d != c or d == 0`` second clause rejects a zero-value entry.

    This is also the specification behind ``JournalEntry.__post_init__``
    mutants 8 and 13, which drop the ``ZERO`` start value from the two ``sum``
    calls: with no lines both sums are integer ``0``, and ``Decimal(0) != 0`` is
    false, so the empty case is caught by the second clause rather than the
    first. ``mutation_triage.json`` records them as equivalent on that proof.
    """
    raises_value(
        "empty: debits 0 != credits 0 (or empty entry)",
        lambda: L.JournalEntry("empty", ENTITY, 1, "x", (), TS, TS),
    )


def test_entry_rejects_a_posted_at_before_its_event_time() -> None:
    """Kills ``JournalEntry.__post_init__`` mutant 24 (the ``event_time`` diagnostic).

    An entry cannot be posted before the event it records, or the books claim to
    know something about a period that had not happened yet.
    """
    raises_value(
        "early: require event_time <= posted_at",
        lambda: L.make(
            "early",
            ENTITY,
            1,
            "sale_on_credit",
            {"ar": "1.00"},
            {"revenue": "1.00"},
            "2025-03-02T00:00:00Z",
            "2025-03-01T00:00:00Z",
        ),
    )


def test_entry_reserves_period_zero_for_opening_entries() -> None:
    """Kills ``JournalEntry.__post_init__`` mutant 31 (the period-0 diagnostic).

    Period 0 is a dedicated opening-balance period: ``(period == 0) != (event ==
    "opening")`` enforces the correspondence in both directions, so an ordinary
    entry cannot occupy period 0 and an opening entry cannot sit anywhere else.
    Mutant 31 drops the message and leaves the correspondence untested.
    """
    raises_value(
        "not-opening: period 0 is reserved for, and required by, opening entries",
        lambda: entry("not-opening", 0, "sale_on_credit", {"ar": "1.00"}, {"revenue": "1.00"}),
    )
    raises_value(
        "not-period-0: period 0 is reserved for, and required by, opening entries",
        lambda: entry("not-period-0", 1, "opening", {"cash": "1.00"}, {"common_stock": "1.00"}),
    )


# ================================================================ balances
def test_balances_skips_excluded_entries_without_stopping() -> None:
    """Kills ``balances`` mutant 11 (the ``exclude`` guard's ``continue`` -> ``break``).

    An opening entry is a period-0 entry posted into a book that already has
    ordinary periods, which is the normal load order, so it lands *before* them in
    ``ledger.entries``. A ``break`` on the excluded entry then abandons every
    entry after it: the whole operating history silently disappears from the
    balance sheet rather than raising.
    """
    led = book()
    led.post(entry("opening-1", 0, "opening", {"cash": "500.00"}, {"common_stock": "500.00"}))
    post(led, "sale-1", 1, "sale_on_credit", "1000.00")

    filtered = led.balances(exclude=frozenset({"opening"}))

    assert filtered["revenue"] == D("1000.00"), "the sale must still be counted"
    assert filtered["cash"] == D(0), "the opening entry must be excluded"
    assert led.balances()["revenue"] == D("1000.00")
    assert led.balances()["cash"] == D("500.00")


# =================================================================== event
def test_event_rejects_a_mapping_with_the_wrong_accounts() -> None:
    """Kills ``event`` mutants 8 and 9 (the ``requires exactly accounts`` diagnostic).

    Mutant 8 replaces the message with ``None``; mutant 9 formats
    ``sorted(None)`` instead of the expected account set, which raises ``TypeError``
    from inside the diagnostic. A per-account mapping that does not match the
    event's template would post a partial entry, so the check must hold *and* say
    which accounts it wanted.
    """
    raises_value(
        "sale_on_credit requires exactly accounts ['ar', 'revenue']",
        lambda: L.event("x", ENTITY, 1, "sale_on_credit", {"ar": "1.00"}, TS, TS),
    )
    raises_value(
        "buy_inventory_on_credit requires exactly accounts ['ap', 'inventory']",
        lambda: L.event("x", ENTITY, 1, "buy_inventory_on_credit", {"ap": "1.00"}, TS, TS),
    )


def test_event_rejects_a_scalar_amount_for_a_multi_account_event() -> None:
    """Kills ``event`` mutants 12 and 17 (the ``per-account amount mapping`` guard).

    ``issue_common_shares`` credits two accounts, so a single scalar cannot
    populate both. Mutant 12 weakens ``or`` to ``and`` and lets the call through,
    which silently credits only the first account and drops the paid-in capital
    entirely; mutant 17 discards the diagnostic.
    """
    raises_value(
        "issue_common_shares requires a per-account amount mapping",
        lambda: L.event("x", ENTITY, 1, "issue_common_shares", "100.00", TS, TS),
    )


def test_event_builds_a_multi_account_entry_from_a_mapping() -> None:
    """The counterpart to the guard above: the accepted form must be correct.

    A scalar that is wrongly accepted drops the paid-in-capital credit, so the
    entry still balances -- against the wrong accounts, with 100.00 of
    contributed capital recorded as share capital. Pinning the real output is what
    makes mutant 12 a failure rather than a silence.
    """
    built = L.event(
        "x",
        ENTITY,
        1,
        "issue_common_shares",
        {"cash": "500.00", "common_stock": "400.00", "additional_paid_in_capital": "100.00"},
        TS,
        TS,
    )
    assert nets(built) == {
        "cash": D("500.00"),
        "common_stock": D("-400.00"),
        "additional_paid_in_capital": D("-100.00"),
    }


# ============================================== derived statements (reports)
def reporting_ledger() -> L.Ledger:
    """A funded company with one period touching every account class."""
    led = book()
    led.post(entry("open-0", 0, "opening", {"cash": "10000.00"}, {"common_stock": "10000.00"}))
    for entry_id, name, amount in (
        ("sale-1", "sale_on_credit", "1000.00"),
        ("buy-1", "buy_inventory_on_credit", "400.00"),
        ("ship-1", "ship_goods", "400.00"),
        ("sga-1", "pay_sga", "150.00"),
        ("dep-1", "depreciate", "50.00"),
        ("int-1", "pay_interest", "30.00"),
        ("tax-1", "accrue_tax", "40.00"),
        ("div-1", "pay_dividend", "60.00"),
    ):
        post(led, entry_id, 1, name, amount)
    return led


def test_income_statement_publishes_its_documented_keys() -> None:
    """Kills the 14 ``income_statement`` key-rename mutants (26-49).

    A statement is a published contract. Renaming ``gross_profit`` to
    ``GROSS_PROFIT`` or dropping ``cogs`` leaves every number intact and every
    downstream consumer reading ``None``; nothing else in the kernel notices,
    which is exactly why the key set is pinned here by name and by order.
    """
    reported = L.income_statement(reporting_ledger(), 1)

    assert tuple(reported) == IS_KEYS


def test_income_statement_computes_every_line_from_its_inputs() -> None:
    """The arithmetic behind the report: each line is derived, not restated.

    Pins the dependency chain revenue -> cogs -> gross profit -> operating
    expenses -> operating income -> interest -> pretax -> tax -> net income, so a
    mutant that reorders or skips a subtraction changes a number.
    """
    led = reporting_ledger()

    reported = L.income_statement(led, 1)
    b = led.balances(only=1, exclude=frozenset({"opening", "close"}))

    assert reported["revenue"] == b["revenue"] == D("1000.00")
    assert reported["cogs"] == b["cogs"] == D("400.00")
    assert reported["gross_profit"] == D("600.00")
    assert reported["sga"] == b["sga"] == D("150.00")
    assert reported["depreciation"] == b["depreciation"] == D("50.00")
    assert reported["operating_income"] == D("400.00")
    assert reported["interest"] == b["interest"] == D("30.00")
    assert reported["pretax_income"] == D("370.00")
    assert reported["tax_expense"] == b["tax_expense"] == D("40.00")
    assert reported["net_income"] == D("330.00")


def test_income_statement_excludes_opening_and_close_entries() -> None:
    """A closed period's income statement must report the same result.

    The close entry zeroes the P&L accounts and moves the result into retained
    earnings, so an income statement that did not exclude ``opening``/``close``
    would report a result that depends on whether the period had been closed.
    """
    led = reporting_ledger()
    before = L.income_statement(led, 1)

    led.close(1, TS)
    after = L.income_statement(led, 1)

    assert after == before
    assert after["net_income"] == D("330.00")


def test_balance_sheet_publishes_its_documented_keys() -> None:
    """Kills ``balance_sheet`` mutants 81 and 82 (the ``ppe_net`` key rename).

    ``ppe_net`` is the one *derived* line on the balance sheet -- gross PP&E less
    accumulated depreciation -- and it is what a reader uses to check the
    depreciation policy against the asset account. Renaming it is a silent
    contract break, so the key is pinned alongside every other one.
    """
    reported = L.balance_sheet(reporting_ledger(), 1)

    assert "ppe_net" in reported
    assert reported["ppe_net"] == D("-50.00")
    assert set(reported) == {
        "cash",
        "ar",
        "inventory",
        "ppe",
        "acc_dep",
        "ap",
        "tax_payable",
        "interest_payable",
        "debt",
        "common_stock",
        "additional_paid_in_capital",
        "treasury_stock",
        "retained_earnings",
        "ppe_net",
        "total_assets",
        "total_liabilities",
        "total_equity",
    }


def test_balance_sheet_subtracts_dividends_from_equity() -> None:
    """Kills ``balance_sheet`` mutant 40 (``- b["dividends"]`` -> ``+ b["dividends"]``).

    Dividends are a debit-normal equity account, so they reduce the balance the
    ledger reports. Adding them instead overstates equity by twice the declared
    dividend, and the accounting identity ``assets == liabilities + equity``
    breaks -- which is the cheapest way to notice, and the reason this test
    asserts the identity as well as the number.
    """
    led = reporting_ledger()
    with_dividend = led.balances(only=1)["dividends"]
    assert with_dividend == D("60.00"), "precondition: a declared dividend"

    reported = L.balance_sheet(led, 1)

    assert reported["total_equity"] == D("10270.00")
    assert reported["total_assets"] == reported["total_liabilities"] + reported["total_equity"], (
        "assets must equal liabilities plus equity"
    )
    # And the same identity holds with no dividend at all, which is what makes
    # the sign a real observation rather than an accident of the fixture.
    undividended = book()
    undividended.post(entry("open-0", 0, "opening", {"cash": "10000.00"}, {"common_stock": "10000.00"}))
    post(undividended, "sale-1", 1, "sale_on_credit", "1000.00")
    plain = L.balance_sheet(undividended, 1)
    assert plain["total_equity"] == D("11000.00"), "10000 issued + 1000 unclosed income"
    assert plain["total_assets"] == plain["total_liabilities"] + plain["total_equity"]


def test_balance_sheet_carries_unclosed_income_into_equity() -> None:
    """Unclosed net income belongs in equity, not in retained earnings.

    ``retained_earnings`` is reported at zero while the P&L accounts still hold
    their balances; the difference is the ``unclosed_ni`` term. A balance sheet
    that reported the period's profit as retained earnings before the close
    would double-count it the moment the period is actually closed.
    """
    led = reporting_ledger()
    reported = L.balance_sheet(led, 1)
    assert led.balances(only=1)["revenue"] == D("1000.00"), "precondition: still unclosed"

    assert reported["retained_earnings"] == D(0)
    # 10000 issued + 330 unclosed net income - 60 declared dividend.
    assert reported["total_equity"] == D("10270.00")
    assert reported["total_assets"] == reported["total_liabilities"] + reported["total_equity"]

    led.close(1, TS)
    closed = L.balance_sheet(led, 1)
    assert closed["retained_earnings"] == D("270.00"), "net income 330 less the dividend 60"
    # Closing moves the P&L into retained earnings and leaves equity unchanged.
    assert closed["total_equity"] == D("10270.00")
    assert closed["total_assets"] == closed["total_liabilities"] + closed["total_equity"]


def test_cash_flow_direct_classifies_cash_by_its_counterparty() -> None:
    """Kills ``cash_flow_direct`` mutants 10, 24 and 25.

    Each cash posting is classified by the ``cf_class`` of the *other* accounts
    it touches, never by the cash leg's own class. Mutant 24 and 25 misspell the
    ``"cash"`` literal in that exclusion, and mutant 10 drops the ``ZERO`` start
    from the per-entry sum; the operating/investing/financing split below is
    only right if all three hold.
    """
    led = reporting_ledger()
    post(led, "capex-1", 1, "capex", "300.00")
    post(
        led,
        "issue-1",
        1,
        "issue_common_shares",
        {"cash": "500.00", "common_stock": "400.00", "additional_paid_in_capital": "100.00"},
    )

    reported = L.cash_flow_direct(led, 1)

    assert set(reported) == {"operating", "investing", "financing"}
    # Cash: +10,000 opening is period 0; period 1 is -150 sga -30 interest
    # -60 dividend +300 capex +500 share issuance.
    assert reported["operating"] == D("-180.00"), "-150 sga, -30 interest"
    assert reported["investing"] == D("-300.00"), "capex is cash out"
    assert reported["financing"] == D("440.00"), "+500 share issuance, -60 dividend"


def test_cash_flow_indirect_agrees_with_the_direct_method_on_no_working_capital() -> None:
    """Kills ``cash_flow_indirect`` mutants 17 and 42.

    With no working-capital movement the indirect reconciliation must reproduce
    the direct one exactly. Mutant 42 flips the sign on the paid-in-capital term,
    which shows up as a financing figure that is 200.00 light on the share
    issuance; mutant 17 drops the ``ZERO`` start from the non-cash add-back sum.
    """
    led = reporting_ledger()
    post(led, "capex-1", 1, "capex", "300.00")
    post(
        led,
        "issue-1",
        1,
        "issue_common_shares",
        {"cash": "500.00", "common_stock": "400.00", "additional_paid_in_capital": "100.00"},
    )

    direct = L.cash_flow_direct(led, 1)
    indirect = L.cash_flow_indirect(led, 1)

    assert set(indirect) == {"operating", "investing", "financing"}
    for section in ("operating", "investing", "financing"):
        assert indirect[section] == direct[section], section
    # Financing: +400 share capital +100 paid-in capital -60 declared dividend.
    # Flipping the paid-in-capital sign makes it 240.00 light.
    assert indirect["financing"] == D("440.00")
    # Operating: net income 330 +50 depreciation add-back -560 working capital.
    assert indirect["operating"] == D("-180.00")
    assert indirect["investing"] == D("-300.00")
