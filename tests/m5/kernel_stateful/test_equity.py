from __future__ import annotations

import copy
from datetime import date, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule
from machine_settings import machine_settings

from fwf_kernel import equity as E
from fwf_kernel import ledger as L

D = Decimal
ENTITY = "ENT-EQUITY"
OPENING_TS = "2025-01-01T00:00:00Z"


def ts(period: int, day: int = 1) -> tuple[str, str]:
    event_date = date(2025, period, day)
    posted_date = event_date + timedelta(days=1)
    return (
        f"{event_date.isoformat()}T00:00:00Z",
        f"{posted_date.isoformat()}T00:00:00Z",
    )


def opening_book(
    *,
    issued: int = 100,
    treasury: int = 0,
    cash: str = "1000.00",
    par: str = "1.00",
    apic: str = "0.00",
    treasury_stock: str = "0.00",
) -> E.EquityBook:
    ledger = L.Ledger(ENTITY)
    ledger.post(
        L.make(
            "equity-opening",
            ENTITY,
            0,
            "opening",
            {"cash": cash, "treasury_stock": treasury_stock},
            {
                "common_stock": D(par) * issued,
                "additional_paid_in_capital": apic,
                "retained_earnings": D(cash) - D(par) * issued - D(apic) + D(treasury_stock),
            },
            OPENING_TS,
            OPENING_TS,
        )
    )
    ledger.close(0, OPENING_TS)
    return E.EquityBook(
        ledger,
        E.CommonShareClass("common", D(par)),
        issued_shares=issued,
        treasury_shares=treasury,
    )


def assert_identity(book: E.EquityBook, period: int = 1) -> None:
    balance_sheet = L.balance_sheet(book.ledger, period)
    assert (
        balance_sheet["total_assets"]
        == balance_sheet["total_liabilities"] + balance_sheet["total_equity"]
    )
    assert book.validate_reconciliations() == set()


def entry_lines(book: E.EquityBook) -> tuple[L.Line, ...]:
    return book.ledger.entries[-1].lines


def test_equity_accounts_and_rule_registry_are_explicit() -> None:
    assert L.CHART["additional_paid_in_capital"].kind is L.Kind.EQUITY
    assert L.CHART["additional_paid_in_capital"].normal is L.Normal.CREDIT
    assert (
        L.CHART["additional_paid_in_capital"].xbrl == "us-gaap:AdditionalPaidInCapitalCommonStock"
    )
    assert L.CHART["treasury_stock"].kind is L.Kind.EQUITY
    assert L.CHART["treasury_stock"].normal is L.Normal.DEBIT
    assert L.CHART["treasury_stock"].xbrl == "us-gaap:TreasuryStockValue"
    assert L.EVENTS["issue_common_shares"] == (
        ("cash",),
        ("common_stock", "additional_paid_in_capital"),
    )
    assert L.EVENTS["repurchase_common_shares"] == (("treasury_stock",), ("cash",))
    assert tuple(E.RECONCILIATION_RULES) == (
        "ACCT-EQUITY-SUBLEDGER",
        "ACCT-WASO",
        "ACCT-BASIC-EPS",
    )


def test_opening_shares_reconcile_without_fake_issuance() -> None:
    book = opening_book(issued=100, apic="20.00")
    assert len(book.ledger.entries) == 1
    assert book.share_counts() == E.ShareCounts(100, 0, 100)
    assert book.issued_shares == 100
    assert book.treasury_shares == 0
    assert book.outstanding_shares == 100
    assert book.validate_reconciliations() == set()
    assert_identity(book, 0)


def test_opening_equity_can_include_treasury_stock_and_apic() -> None:
    book = opening_book(
        issued=100,
        treasury=20,
        cash="1000.00",
        apic="20.00",
        treasury_stock="40.00",
    )
    assert book.share_counts() == E.ShareCounts(100, 20, 80)
    assert book.ledger.balances()["treasury_stock"] == D("40.00")
    assert book.ledger.balances()["additional_paid_in_capital"] == D("20.00")
    assert book.validate_reconciliations() == set()
    assert_identity(book, 0)


def test_issuance_at_par_posts_cash_and_common_stock_only() -> None:
    book = opening_book()
    event = book.issue_common_shares(50, "1.00", date(2025, 1, 15), 1, *ts(1, 15))
    assert event.kind is E.ShareEventKind.ISSUANCE
    assert entry_lines(book) == (
        L.Line("cash", debit=D("50.00")),
        L.Line("common_stock", credit=D("50.00")),
        L.Line("additional_paid_in_capital", credit=D("0.00")),
    )
    assert book.share_counts() == E.ShareCounts(150, 0, 150)
    assert book.ledger.balances()["additional_paid_in_capital"] == D("0.00")
    assert L.cash_flow_direct(book.ledger, 1) == L.cash_flow_indirect(book.ledger, 1)
    assert_identity(book)


def test_issuance_above_par_posts_apic_and_preserves_balance() -> None:
    book = opening_book()
    book.issue_common_shares(50, "2.00", date(2025, 1, 15), 1, *ts(1, 15))
    balances = book.ledger.balances()
    assert balances["cash"] == D("1100.00")
    assert balances["common_stock"] == D("150.00")
    assert balances["additional_paid_in_capital"] == D("50.00")
    assert book.validate_reconciliations() == set()
    assert_identity(book)


def test_repurchase_uses_treasury_stock_and_never_touches_pnl() -> None:
    book = opening_book(issued=100, cash="1000.00")
    book.repurchase_common_shares(20, "2.00", date(2025, 2, 1), 1, *ts(2, 1))
    balances = book.ledger.balances()
    assert balances["cash"] == D("960.00")
    assert balances["treasury_stock"] == D("40.00")
    assert balances["common_stock"] == D("100.00")
    assert book.share_counts() == E.ShareCounts(100, 20, 80)
    assert book.validate_reconciliations() == set()
    assert all(value == D("0.00") for value in L.income_statement(book.ledger, 1).values())
    assert L.cash_flow_direct(book.ledger, 1) == L.cash_flow_indirect(book.ledger, 1)
    assert_identity(book)


def test_repurchase_rejects_over_repurchase_and_insufficient_cash_without_mutation() -> None:
    book = opening_book(issued=100, cash="1000.00")
    before_entries = copy.deepcopy(book.ledger.entries)
    before_counts = book.share_counts()
    with pytest.raises(ValueError, match="exceeds outstanding"):
        book.repurchase_common_shares(101, "1.00", date(2025, 1, 2), 1, *ts(1, 2))
    with pytest.raises(ValueError, match="insufficient cash"):
        book.repurchase_common_shares(1, "2000.00", date(2025, 1, 2), 1, *ts(1, 2))
    assert book.ledger.entries == before_entries
    assert book.share_counts() == before_counts
    assert book.validate_reconciliations() == set()


def test_issuance_rejects_below_par_nonintegral_and_float_counts_without_mutation() -> None:
    book = opening_book()
    before_entries = copy.deepcopy(book.ledger.entries)
    for shares, price in ((1, "0.99"), (D("1.5"), "1.00"), (1.0, "1.00")):
        with pytest.raises((TypeError, ValueError)):
            book.issue_common_shares(shares, price, date(2025, 1, 2), 1, *ts(1, 2))
    assert book.ledger.entries == before_entries
    assert book.validate_reconciliations() == set()


def test_waso_mid_period_issuance_uses_inclusive_daily_weighting() -> None:
    book = opening_book(issued=100, cash="1000.00")
    book.issue_common_shares(100, "1.00", date(2025, 2, 15), 1, *ts(2, 15))
    assert book.weighted_average_shares(date(2025, 1, 1), date(2025, 3, 31)) == D("150")


def test_waso_mid_period_repurchase_uses_inclusive_daily_weighting() -> None:
    book = opening_book(issued=200, cash="1000.00")
    book.repurchase_common_shares(50, "1.00", date(2025, 1, 31), 1, *ts(1, 31))
    with localcontext() as context:
        context.prec = 60
        expected = D(15000) / D(90)
    assert book.weighted_average_shares(date(2025, 1, 1), date(2025, 3, 31)) == expected


def test_two_for_one_split_preserves_common_stock_and_adjusts_prior_waso() -> None:
    book = opening_book(issued=100, cash="1000.00")
    entries_before = len(book.ledger.entries)
    book.split_common_shares(Fraction(2, 1), date(2025, 4, 1), 1)
    assert len(book.ledger.entries) == entries_before
    assert book.share_counts() == E.ShareCounts(200, 0, 200)
    assert book.par_value == D("0.50")
    assert book.ledger.balances()["common_stock"] == D("100.00")
    assert book.weighted_average_shares(
        date(2025, 1, 1), date(2025, 3, 31), as_of_date=date(2025, 3, 31)
    ) == D("100")
    assert book.weighted_average_shares(
        date(2025, 1, 1), date(2025, 3, 31), as_of_date=date(2025, 4, 1)
    ) == D("200")
    assert book.validate_reconciliations() == set()


def test_reverse_and_three_for_two_splits_use_exact_rational_arithmetic() -> None:
    reverse = opening_book(issued=100)
    reverse.split_common_shares(Fraction(1, 5), date(2025, 4, 1), 1)
    assert reverse.share_counts() == E.ShareCounts(20, 0, 20)
    assert reverse.par_value == D("5.00")
    assert reverse.validate_reconciliations() == set()

    three_for_two = opening_book(issued=200)
    three_for_two.split_common_shares(Fraction(3, 2), date(2025, 4, 1), 1)
    assert three_for_two.share_counts() == E.ShareCounts(300, 0, 300)
    assert three_for_two.validate_reconciliations() == set()

    invalid = opening_book(issued=99)
    with pytest.raises(ValueError, match="fractional shares"):
        invalid.split_common_shares(Fraction(3, 2), date(2025, 4, 1), 1)
    assert invalid.validate_reconciliations() == set()


def test_basic_eps_handles_negative_income_precision_and_zero_denominator() -> None:
    book = opening_book(issued=100, cash="1000.00")
    assert book.basic_eps(D("100.00"), date(2025, 1, 1), date(2025, 1, 31)) == D("1.000000")
    assert book.basic_eps(D("-100.00"), date(2025, 1, 1), date(2025, 1, 31)) == D("-1.000000")
    precision = opening_book(issued=3, cash="100.00")
    assert precision.basic_eps(D("1.00"), date(2025, 1, 1), date(2025, 1, 3)) == D("0.333333")

    no_shares = opening_book(issued=0, cash="100.00")
    with pytest.raises(ValueError, match="denominator"):
        no_shares.basic_eps(D("1.00"), date(2025, 1, 1), date(2025, 1, 31))


def test_equity_waso_and_eps_corruption_detection() -> None:
    book = opening_book(issued=100, apic="20.00")
    corrupted = copy.deepcopy(book)
    object.__setattr__(corrupted, "_issued_shares", 99)
    assert corrupted.validate_reconciliations() == {E.EQUITY_RULE}
    corrupted_apic = copy.deepcopy(book)
    object.__setattr__(corrupted_apic, "_apic_balance", D("19.00"))
    assert corrupted_apic.validate_reconciliations() == {E.EQUITY_RULE}

    assert book.validate_waso(date(2025, 1, 1), date(2025, 1, 31), reported=D("99")) == {
        E.WASO_RULE
    }
    assert book.validate_basic_eps(
        D("100.00"), date(2025, 1, 1), date(2025, 1, 31), reported=D("2.000000")
    ) == {E.BASIC_EPS_RULE}
    assert book.validate_reconciliations(
        period_start=date(2025, 1, 1),
        period_end=date(2025, 1, 31),
        reported_waso=D("99"),
        net_income=D("100.00"),
        reported_eps=D("2.000000"),
    ) == {E.WASO_RULE, E.BASIC_EPS_RULE}


class EquityMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.book = opening_book(issued=1000, cash="100000.00")

    @rule(shares=st.integers(1, 100), cents=st.integers(100, 300))
    def issue(self, shares: int, cents: int) -> None:
        self.book.issue_common_shares(
            shares,
            D(cents) / D(100),
            date(2025, 1, 1),
            1,
            *ts(1),
        )

    @precondition(
        lambda state: state.book.outstanding_shares > 0 and state.book.ledger.balances()["cash"] > 0
    )
    @rule(shares=st.integers(1, 100), cents=st.integers(100, 300))
    def repurchase(self, shares: int, cents: int) -> None:
        count = min(shares, self.book.outstanding_shares)
        self.book.repurchase_common_shares(
            count,
            D(cents) / D(100),
            date(2025, 1, 2),
            1,
            *ts(1, 2),
        )

    @invariant()
    def equity_invariants_hold(self) -> None:
        assert self.book.validate_reconciliations() == set()
        assert self.book.issued_shares >= self.book.treasury_shares >= 0
        assert self.book.outstanding_shares == self.book.issued_shares - self.book.treasury_shares
        assert all(value == D("0.00") for value in L.income_statement(self.book.ledger, 1).values())
        assert_identity(self.book)


TestEquityMachine = EquityMachine.TestCase
TestEquityMachine.settings = machine_settings(max_examples=30, stateful_step_count=25)
