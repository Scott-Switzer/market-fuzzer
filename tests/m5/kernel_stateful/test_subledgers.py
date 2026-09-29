from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule
from machine_settings import machine_settings

from fwf_kernel import ledger as L
from fwf_kernel import subledgers as S

D = Decimal
TS = "2025-01-01T00:00:00Z"
ENTITY = "ENT-M41"


def ts(period: int) -> tuple[str, str]:
    month = f"{period:02d}"
    return (
        f"2025-{month}-01T00:00:00Z",
        f"2025-{month}-02T00:00:00Z",
    )


def operational_book(cash: str = "1000.00") -> S.OperationalBook:
    ledger = L.Ledger(ENTITY)
    ledger.post(
        L.make(
            "opening-cash",
            ENTITY,
            0,
            "opening",
            {"cash": cash},
            {"common_stock": cash},
            TS,
            TS,
        )
    )
    ledger.close(0, TS)
    return S.OperationalBook(ledger)


def opening_detail_book() -> S.OperationalBook:
    ledger = L.Ledger(ENTITY)
    ledger.post(
        L.make(
            "opening-detail",
            ENTITY,
            0,
            "opening",
            {"cash": "1000.00", "ar": "100.00", "inventory": "200.00", "ppe": "300.00"},
            {
                "ap": "80.00",
                "debt": "400.00",
                "common_stock": "700.00",
                "retained_earnings": "420.00",
            },
            TS,
            TS,
        )
    )
    ledger.close(0, TS)
    return S.OperationalBook(
        ledger,
        receivables=(S.ReceivableInvoice("open-ar", ENTITY, 0, 0, D("100.00"), D("100.00")),),
        payables=(S.PayableInvoice("open-ap", ENTITY, 0, 0, D("80.00"), D("80.00")),),
        inventory_layers=(S.InventoryLayer("open-layer", 0, D("200.00"), D("200.00")),),
        ppe_assets=(S.PPEAsset("open-ppe", ENTITY, 0, D("300.00"), D("30.00"), 3, D("0.00")),),
        debt_tranches=(
            S.DebtTranche("open-debt", ENTITY, 0, 4, D("400.00"), D("400.00"), D("0.12")),
        ),
    )


def operational_state(book: S.OperationalBook) -> tuple[object, ...]:
    return (
        copy.deepcopy(book.ledger.entries),
        copy.deepcopy(book.receivables),
        copy.deepcopy(book.payables),
        copy.deepcopy(book.inventory_layers),
        copy.deepcopy(book.ppe_assets),
        copy.deepcopy(book.debt_tranches),
        copy.deepcopy(book.accrued_interest),
    )


def assert_posted(
    book: S.OperationalBook,
    event: str,
    debit_account: str,
    credit_account: str,
    amount: str,
) -> None:
    entry = book.ledger.entries[-1]
    assert entry.event == event
    assert entry.lines == (
        L.Line(debit_account, debit=D(amount)),
        L.Line(credit_account, credit=D(amount)),
    )
    assert sum((line.debit for line in entry.lines), D("0.00")) == sum(
        (line.credit for line in entry.lines), D("0.00")
    )


def corrupt_outstanding(
    collection: dict[str, S.ReceivableInvoice] | dict[str, S.PayableInvoice],
    record_id: str,
    amount: str,
) -> None:
    invoice = collection[record_id]
    object.__setattr__(invoice, "_outstanding_amount", D(amount))


def test_cross_entity_creation_attempts_are_rejected_without_any_mutation() -> None:
    book = operational_book()
    before = operational_state(book)
    calls = (
        lambda: book.issue_receivable("bad-ar", "OTHER", 1, 1, "10.00", *ts(1)),
        lambda: book.issue_inventory_payable("bad-ap", "bad-layer", "OTHER", 1, 1, "10.00", *ts(1)),
        lambda: book.acquire_ppe_asset("bad-ppe", "OTHER", 1, "10.00", "0.00", 3, *ts(1)),
        lambda: book.borrow_debt("bad-debt", "OTHER", 1, 2, "10.00", "0.10", *ts(1)),
    )
    for call in calls:
        with pytest.raises(ValueError, match="subledger record belongs to another entity"):
            call()
        assert operational_state(book) == before


def test_opening_detail_bootstrap_reconciles_and_supports_period_zero_operations() -> None:
    book = opening_detail_book()
    assert len(book.ledger.entries) == 1
    assert book.validate_reconciliations() == set()
    balance_sheet = L.balance_sheet(book.ledger, 0)
    assert (
        balance_sheet["total_assets"]
        == balance_sheet["total_liabilities"] + balance_sheet["total_equity"]
    )

    assert book.collect_receivable("open-ar", "30.00", 1, *ts(1)) == D("30.00")
    assert book.pay_payable("open-ap", "50.00", 1, *ts(1)) == D("50.00")
    assert book.consume_inventory("20.00", 1, *ts(1)) == D("20.00")
    assert book.depreciate_ppe(1, *ts(1)) == D("90.00")
    assert book.accrue_interest("open-debt", 1, *ts(1)) == D("12.00")
    assert book.validate_reconciliations() == set()
    balances = book.ledger.balances()
    assert balances["ar"] == book.receivables["open-ar"].outstanding_amount
    assert balances["ap"] == book.payables["open-ap"].outstanding_amount
    assert balances["inventory"] == book.inventory_layers["open-layer"].remaining_cost
    assert balances["ppe"] == book.ppe_assets["open-ppe"].gross_cost
    assert balances["acc_dep"] == book.ppe_assets["open-ppe"].accumulated_depreciation
    assert balances["debt"] == book.debt_tranches["open-debt"].principal_outstanding
    assert balances["interest_payable"] == D("12.00")
    final_balance_sheet = L.balance_sheet(book.ledger, 1)
    assert (
        final_balance_sheet["total_assets"]
        == final_balance_sheet["total_liabilities"] + final_balance_sheet["total_equity"]
    )


def test_opening_detail_constructor_rejects_duplicates_and_mismatches() -> None:
    invoice = S.ReceivableInvoice("open-ar", ENTITY, 0, 0, D("100.00"), D("100.00"))
    with pytest.raises(ValueError, match="duplicate detail record"):
        S.OperationalBook(
            L.Ledger(ENTITY),
            receivables=(invoice, invoice),
        )
    with pytest.raises(ValueError, match="invalid operational book"):
        S.OperationalBook(
            L.Ledger(ENTITY),
            receivables=(S.ReceivableInvoice("open-ar", ENTITY, 0, 0, D("1.00"), D("1.00")),),
        )
    with pytest.raises(ValueError, match="mapping key"):
        S.OperationalBook(L.Ledger(ENTITY), receivables={"wrong-key": invoice})


def test_normal_creation_methods_still_reject_period_zero() -> None:
    book = operational_book()
    with pytest.raises(ValueError, match="operating period"):
        book.issue_receivable("ar-zero", ENTITY, 0, 0, "1.00", *ts(1))
    with pytest.raises(ValueError, match="operating period"):
        book.issue_inventory_payable("ap-zero", "layer-zero", ENTITY, 0, 0, "1.00", *ts(1))
    with pytest.raises(ValueError, match="operating period"):
        book.acquire_ppe_asset("ppe-zero", ENTITY, 0, "1.00", "0.00", 2, *ts(1))
    with pytest.raises(ValueError, match="operating period"):
        book.borrow_debt("debt-zero", ENTITY, 0, 1, "1.00", "0.10", *ts(1))
    assert book.validate_reconciliations() == set()


def test_interest_payable_account_and_balance_sheet_inclusion() -> None:
    account = L.CHART["interest_payable"]
    assert account.kind is L.Kind.LIABILITY
    assert account.normal is L.Normal.CREDIT
    assert account.cf_class is L.CF.OPERATING
    assert account.xbrl == "us-gaap:InterestPayableCurrent"

    book = operational_book()
    book.borrow_debt("debt-1", ENTITY, 1, 2, "400.00", "0.12", *ts(1))
    book.accrue_interest("debt-1", 2, *ts(2))
    balance_sheet = L.balance_sheet(book.ledger, 2)
    assert balance_sheet["interest_payable"] == D("12.00")
    assert balance_sheet["total_liabilities"] == D("412.00")


def test_m41_rule_registry_is_explicit_and_exact() -> None:
    assert set(S.RECONCILIATION_RULES) == {
        S.AR_RULE,
        S.AP_RULE,
        S.INVENTORY_RULE,
        S.PPE_RULE,
        S.DEBT_RULE,
    }
    assert tuple(S.RECONCILIATION_RULES) == (
        "ACCT-AR-SUBLEDGER",
        "ACCT-AP-SUBLEDGER",
        "ACCT-INVENTORY-FIFO",
        "ACCT-PPE-SCHEDULE",
        "ACCT-DEBT-SCHEDULE",
    )


def test_money_rejects_float_nonfinite_negative_and_zero_where_positive_required() -> None:
    assert S.money("1.005") == D("1.01")
    assert S.money("0") == D("0.00")
    for invalid in (float("1.0"), True):
        with pytest.raises(TypeError, match="binary floats"):
            S.money(invalid)  # type: ignore[arg-type]
    for invalid in ("NaN", "Infinity", "-Infinity", "-0.01"):
        with pytest.raises(ValueError):
            S.money(invalid)
    with pytest.raises(ValueError, match="positive"):
        S.money("0", allow_zero=False)


def test_invoice_identity_is_immutable_and_outstanding_has_no_direct_setter() -> None:
    invoice = S.ReceivableInvoice("ar-1", ENTITY, 1, 1, D("10.00"), D("10.00"))
    with pytest.raises(FrozenInstanceError):
        invoice.invoice_id = "changed"  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        invoice.outstanding_amount = D("9.00")  # type: ignore[misc]


def test_record_constructors_reject_invalid_operational_state() -> None:
    with pytest.raises(ValueError, match="due_period"):
        S.ReceivableInvoice("ar-1", ENTITY, 1, 0, D("10.00"), D("10.00"))
    with pytest.raises(ValueError, match="outstanding"):
        S.PayableInvoice("ap-1", ENTITY, 1, 1, D("10.00"), D("10.01"))
    with pytest.raises(ValueError, match="remaining_cost"):
        S.InventoryLayer("layer-1", 1, D("10.00"), D("10.01"))
    with pytest.raises(ValueError, match="residual_value"):
        S.PPEAsset("asset-1", ENTITY, 1, D("10.00"), D("10.00"), 2, D("0.00"))
    with pytest.raises(ValueError, match="useful_life_periods"):
        S.PPEAsset("asset-1", ENTITY, 1, D("10.00"), D("0.00"), 0, D("0.00"))
    with pytest.raises(ValueError, match="maturity_period"):
        S.DebtTranche("debt-1", ENTITY, 1, 1, D("10.00"), D("10.00"), D("0.10"))
    with pytest.raises(ValueError, match="non-negative"):
        S.DebtTranche("debt-1", ENTITY, 1, 2, D("10.00"), D("10.00"), D("-0.10"))


def test_ar_partial_and_full_collection_post_exactly_and_retain_zero_invoice() -> None:
    book = operational_book()
    invoice = book.issue_receivable("ar-1", ENTITY, 1, 1, "100.00", *ts(1))
    assert_posted(book, "sale_on_credit", "ar", "revenue", "100.00")
    assert invoice.original_amount == D("100.00")
    assert invoice.outstanding_amount == D("100.00")

    assert book.collect_receivable("ar-1", "40.00", 2, *ts(2)) == D("40.00")
    assert_posted(book, "collect_ar", "cash", "ar", "40.00")
    assert book.receivables["ar-1"].outstanding_amount == D("60.00")

    assert book.collect_receivable("ar-1", "60.00", 3, *ts(3)) == D("60.00")
    assert book.receivables["ar-1"].outstanding_amount == D("0.00")
    assert "ar-1" in book.receivables
    assert book.validate_reconciliations() == set()
    assert book.ledger.balances()["ar"] == D("0.00")

    with pytest.raises(ValueError, match="exceeds outstanding"):
        book.collect_receivable("ar-1", "0.01", 4, *ts(4))
    assert book.ledger.balances()["cash"] == D("1100.00")


def test_ar_aging_is_pure_and_uses_required_buckets() -> None:
    book = operational_book()
    for index, due_period in enumerate((1, 2, 3, 4), start=1):
        book.issue_receivable(f"ar-{index}", ENTITY, 1, due_period, "10.00", *ts(1))
    book.collect_receivable("ar-1", "4.00", 1, *ts(1))
    before = copy.deepcopy(book.receivables)
    aging = book.receivables_aging(3)
    assert aging == {
        "current": D("20.00"),
        "1-period-past-due": D("10.00"),
        "2-periods-past-due": D("6.00"),
        "3+-periods-past-due": D("0.00"),
    }
    assert book.receivables == before


def test_ar_reconciliation_corruption_is_detected() -> None:
    book = operational_book()
    book.issue_receivable("ar-1", ENTITY, 1, 2, "10.00", *ts(1))
    copied = copy.deepcopy(book)
    corrupt_outstanding(copied.receivables, "ar-1", "11.00")
    assert copied.validate_reconciliations() == {S.AR_RULE}


def test_ap_partial_and_full_payment_create_inventory_and_reconcile() -> None:
    book = operational_book()
    payable = book.issue_inventory_payable("ap-1", "layer-1", ENTITY, 1, 2, "100.00", *ts(1))
    assert_posted(book, "buy_inventory_on_credit", "inventory", "ap", "100.00")
    assert payable.outstanding_amount == D("100.00")
    assert book.inventory_layers["layer-1"].remaining_cost == D("100.00")

    book.pay_payable("ap-1", "25.00", 2, *ts(2))
    assert_posted(book, "pay_ap", "ap", "cash", "25.00")
    assert book.payables["ap-1"].outstanding_amount == D("75.00")
    book.pay_payable("ap-1", "75.00", 3, *ts(3))
    assert book.payables["ap-1"].outstanding_amount == D("0.00")
    assert book.validate_reconciliations() == set()

    with pytest.raises(ValueError, match="exceeds outstanding"):
        book.pay_payable("ap-1", "0.01", 4, *ts(4))
    assert book.ledger.balances()["cash"] == D("900.00")


def test_ap_aging_and_reconciliation_corruption() -> None:
    book = operational_book()
    for index, due_period in enumerate((1, 2, 3, 4), start=1):
        book.issue_inventory_payable(
            f"ap-{index}", f"layer-{index}", ENTITY, 1, due_period, "10.00", *ts(1)
        )
    assert book.payables_aging(3) == {
        "current": D("20.00"),
        "1-period-past-due": D("10.00"),
        "2-periods-past-due": D("10.00"),
        "3+-periods-past-due": D("0.00"),
    }
    copied = copy.deepcopy(book)
    corrupt_outstanding(copied.payables, "ap-1", "9.00")
    assert copied.validate_reconciliations() == {S.AP_RULE}


def test_three_layer_fifo_consumption_and_exact_reconciliation() -> None:
    book = operational_book()
    for layer_id, amount in (("layer-1", "10.00"), ("layer-2", "20.00"), ("layer-3", "30.00")):
        book.issue_inventory_payable(f"ap-{layer_id}", layer_id, ENTITY, 1, 1, amount, *ts(1))

    assert book.consume_inventory("10.00", 1, *ts(1)) == D("10.00")
    assert book.consume_inventory("20.00", 1, *ts(1)) == D("20.00")
    assert book.consume_inventory("7.00", 1, *ts(1)) == D("7.00")
    assert_posted(book, "ship_goods", "cogs", "inventory", "7.00")
    assert [layer.remaining_cost for layer in book.inventory_layers.values()] == [
        D("0.00"),
        D("0.00"),
        D("23.00"),
    ]
    assert book.ledger.balances()["inventory"] == D("23.00")
    assert book.ledger.balances()["cogs"] == D("37.00")
    assert sum(layer.remaining_cost for layer in book.inventory_layers.values()) == D("23.00")
    assert book.validate_reconciliations() == set()

    with pytest.raises(ValueError, match="exceeds available"):
        book.consume_inventory("23.01", 2, *ts(2))
    assert book.ledger.balances()["inventory"] == D("23.00")


def test_inventory_reconciliation_corruption_is_detected() -> None:
    book = operational_book()
    book.issue_inventory_payable("ap-1", "layer-1", ENTITY, 1, 1, "10.00", *ts(1))
    copied = copy.deepcopy(book)
    object.__setattr__(copied.inventory_layers["layer-1"], "_remaining_cost", D("9.00"))
    assert copied.validate_reconciliations() == {S.INVENTORY_RULE}


def test_ppe_straight_line_cent_rounding_and_final_period_absorption() -> None:
    book = operational_book()
    book.acquire_ppe_asset("asset-1", ENTITY, 1, "100.01", "0.01", 3, *ts(1))
    assert_posted(book, "capex", "ppe", "cash", "100.01")

    assert book.depreciate_ppe(1, *ts(1)) == D("33.33")
    assert book.ppe_assets["asset-1"].accumulated_depreciation == D("33.33")
    assert book.depreciate_ppe(2, *ts(2)) == D("33.33")
    assert book.ppe_assets["asset-1"].accumulated_depreciation == D("66.66")
    assert book.depreciate_ppe(3, *ts(3)) == D("33.34")
    assert book.ppe_assets["asset-1"].accumulated_depreciation == D("100.00")
    assert book.depreciate_ppe(4, *ts(4)) == D("0.00")

    balances = book.ledger.balances()
    assert balances["ppe"] == D("100.01")
    assert balances["acc_dep"] == D("100.00")
    assert balances["ppe"] - balances["acc_dep"] == D("0.01")
    assert book.validate_reconciliations() == set()


def test_ppe_cent_rounding_never_exceeds_the_depreciable_base() -> None:
    book = operational_book()
    book.acquire_ppe_asset("asset-1", ENTITY, 1, "0.03", "0.00", 5, *ts(1))

    # 0.03 over 5 periods is 0.006, which rounds up to 0.01 per period; the
    # schedule must saturate at the 0.03 base instead of accumulating 0.04.
    assert book.ppe_assets["asset-1"]._target_accumulation(4) == D("0.03")

    assert book.depreciate_ppe(4, *ts(4)) == D("0.03")
    assert book.ppe_assets["asset-1"].accumulated_depreciation == D("0.03")
    assert book.depreciate_ppe(5, *ts(5)) == D("0.00")
    assert book.ledger.balances()["acc_dep"] == D("0.03")
    assert book.validate_reconciliations() == set()


def test_ppe_does_not_depreciate_before_in_service_period() -> None:
    book = operational_book()
    book.acquire_ppe_asset("asset-1", ENTITY, 2, "10.00", "0.00", 3, *ts(2))
    entry_count = len(book.ledger.entries)
    assert book.depreciate_ppe(1, *ts(1)) == D("0.00")
    assert len(book.ledger.entries) == entry_count
    assert book.ppe_assets["asset-1"].accumulated_depreciation == D("0.00")
    assert book.validate_reconciliations() == set()


def test_ppe_batch_depreciation_retains_individual_allocations() -> None:
    book = operational_book()
    book.acquire_ppe_asset("asset-1", ENTITY, 1, "100.00", "0.00", 4, *ts(1))
    book.acquire_ppe_asset("asset-2", ENTITY, 1, "50.00", "0.00", 2, *ts(1))
    before_entries = len(book.ledger.entries)
    assert book.depreciate_ppe(1, *ts(1)) == D("50.00")
    assert len(book.ledger.entries) == before_entries + 1
    assert book.ppe_assets["asset-1"].accumulated_depreciation == D("25.00")
    assert book.ppe_assets["asset-2"].accumulated_depreciation == D("25.00")


def test_ppe_reconciliation_corruption_is_detected() -> None:
    book = operational_book()
    book.acquire_ppe_asset("asset-1", ENTITY, 1, "10.00", "1.00", 3, *ts(1))
    copied = copy.deepcopy(book)
    object.__setattr__(copied.ppe_assets["asset-1"], "gross_cost", D("11.00"))
    assert copied.validate_reconciliations() == {S.PPE_RULE}


def test_debt_accrual_payment_partial_and_final_principal_repayment() -> None:
    book = operational_book(cash="500.00")
    tranche = book.borrow_debt("debt-1", ENTITY, 1, 4, "100.00", "0.12", *ts(1))
    assert_posted(book, "borrow", "cash", "debt", "100.00")
    assert tranche.principal_outstanding == D("100.00")

    assert book.accrue_interest("debt-1", 2, *ts(2)) == D("3.00")
    assert_posted(book, "accrue_interest", "interest", "interest_payable", "3.00")
    assert book.ledger.balances()["interest_payable"] == D("3.00")
    assert L.cash_flow_direct(book.ledger, 2) == L.cash_flow_indirect(book.ledger, 2)

    book.repay_debt("debt-1", "40.00", 2, *ts(2))
    assert_posted(book, "repay", "debt", "cash", "40.00")
    assert book.debt_tranches["debt-1"].principal_outstanding == D("60.00")
    assert book.accrued_interest[("debt-1", 2)].ending_principal == D("60.00")

    assert book.accrue_interest("debt-1", 3, *ts(3)) == D("1.80")
    assert book.pay_accrued_interest("4.80", 3, *ts(3)) == D("4.80")
    assert_posted(book, "pay_accrued_interest", "interest_payable", "cash", "4.80")
    assert L.cash_flow_direct(book.ledger, 3) == L.cash_flow_indirect(book.ledger, 3)
    assert book.ledger.balances()["interest_payable"] == D("0.00")

    book.repay_debt("debt-1", "60.00", 3, *ts(3))
    assert book.debt_tranches["debt-1"].principal_outstanding == D("0.00")
    assert book.validate_reconciliations() == set()
    with pytest.raises(ValueError, match="exceeds outstanding principal"):
        book.repay_debt("debt-1", "0.01", 4, *ts(4))
    with pytest.raises(ValueError, match="exceeds accrued"):
        book.pay_accrued_interest("0.01", 4, *ts(4))


def test_debt_interest_is_cent_quantized_and_schedule_reconciles() -> None:
    book = operational_book()
    book.borrow_debt("debt-1", ENTITY, 1, 2, "101.00", "0.10", *ts(1))
    assert book.accrue_interest("debt-1", 2, *ts(2)) == D("2.53")
    schedule = book.accrued_interest[("debt-1", 2)]
    assert schedule.opening_principal == D("101.00")
    assert schedule.unpaid_amount == D("2.53")
    assert sum(item.unpaid_amount for item in book.accrued_interest.values()) == D("2.53")
    assert book.ledger.balances()["interest_payable"] == D("2.53")
    assert book.validate_reconciliations() == set()


def test_debt_reconciliation_corruption_is_detected() -> None:
    book = operational_book()
    book.borrow_debt("debt-1", ENTITY, 1, 2, "100.00", "0.10", *ts(1))
    copied = copy.deepcopy(book)
    object.__setattr__(copied.debt_tranches["debt-1"], "_principal_outstanding", D("99.00"))
    assert copied.validate_reconciliations() == {S.DEBT_RULE}

    other = operational_book()
    other.borrow_debt("debt-1", ENTITY, 1, 2, "100.00", "0.10", *ts(1))
    other.accrue_interest("debt-1", 2, *ts(2))
    corrupted_interest = copy.deepcopy(other)
    object.__setattr__(corrupted_interest.accrued_interest[("debt-1", 2)], "paid_amount", D("0.01"))
    assert corrupted_interest.validate_reconciliations() == {S.DEBT_RULE}


def test_unpaid_then_paid_interest_preserves_direct_indirect_cash_flow_identity() -> None:
    book = operational_book()
    book.borrow_debt("debt-1", ENTITY, 1, 3, "400.00", "0.12", *ts(1))
    book.accrue_interest("debt-1", 2, *ts(2))
    assert L.cash_flow_direct(book.ledger, 2)["operating"] == D("0.00")
    assert L.cash_flow_indirect(book.ledger, 2)["operating"] == D("0.00")
    book.pay_accrued_interest("12.00", 3, *ts(3))
    assert L.cash_flow_direct(book.ledger, 3)["operating"] == D("-12.00")
    assert L.cash_flow_indirect(book.ledger, 3)["operating"] == D("-12.00")
    assert L.cash_flow_direct(book.ledger, 3) == L.cash_flow_indirect(book.ledger, 3)


def test_normal_operations_only_create_balanced_operational_entries() -> None:
    book = operational_book()
    book.issue_receivable("ar-1", ENTITY, 1, 1, "20.00", *ts(1))
    book.collect_receivable("ar-1", "5.00", 1, *ts(1))
    book.issue_inventory_payable("ap-1", "layer-1", ENTITY, 1, 1, "30.00", *ts(1))
    book.consume_inventory("10.00", 1, *ts(1))
    book.pay_payable("ap-1", "5.00", 1, *ts(1))
    book.acquire_ppe_asset("asset-1", ENTITY, 1, "40.00", "0.00", 2, *ts(1))
    book.depreciate_ppe(1, *ts(1))
    book.borrow_debt("debt-1", ENTITY, 1, 3, "50.00", "0.08", *ts(1))
    book.accrue_interest("debt-1", 2, *ts(2))
    book.pay_accrued_interest("1.00", 2, *ts(2))
    book.repay_debt("debt-1", "10.00", 2, *ts(2))

    operational_entries = [
        entry for entry in book.ledger.entries if entry.entry_id.startswith("m41-")
    ]
    assert operational_entries
    assert all(
        sum((line.debit for line in entry.lines), D("0.00"))
        == sum((line.credit for line in entry.lines), D("0.00"))
        > D("0.00")
        for entry in operational_entries
    )
    assert book.validate_reconciliations() == set()


class OperationalBookMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.book = operational_book("100000.00")
        self.period = 1
        self.sequence = 0

    def _id(self, prefix: str) -> str:
        self.sequence += 1
        return f"{prefix}-{self.sequence}"

    def _open_receivables(self) -> list[S.ReceivableInvoice]:
        return [item for item in self.book.receivables.values() if item.outstanding_amount > 0]

    def _open_payables(self) -> list[S.PayableInvoice]:
        return [item for item in self.book.payables.values() if item.outstanding_amount > 0]

    @rule(cents=st.integers(1, 10_000), due_offset=st.integers(0, 3))
    def issue_receivable(self, cents: int, due_offset: int) -> None:
        self.book.issue_receivable(
            self._id("ar"),
            ENTITY,
            self.period,
            self.period + due_offset,
            D(cents) / D(100),
            *ts(self.period),
        )

    @precondition(lambda state: bool(state._open_receivables()))
    @rule(index=st.integers(), cents=st.integers(1, 10_000))
    def collect_receivable_partial(self, index: int, cents: int) -> None:
        open_invoices = self._open_receivables()
        invoice = open_invoices[index % len(open_invoices)]
        amount = min(D(cents) / D(100), invoice.outstanding_amount)
        self.book.collect_receivable(invoice.invoice_id, amount, self.period, *ts(self.period))

    @precondition(lambda state: bool(state._open_receivables()))
    @rule(index=st.integers())
    def collect_receivable_full(self, index: int) -> None:
        open_invoices = self._open_receivables()
        invoice = open_invoices[index % len(open_invoices)]
        self.book.collect_receivable(
            invoice.invoice_id, invoice.outstanding_amount, self.period, *ts(self.period)
        )

    @rule(cents=st.integers(1, 10_000), due_offset=st.integers(0, 3))
    def purchase_inventory_on_ap(self, cents: int, due_offset: int) -> None:
        payable_id = self._id("ap")
        self.book.issue_inventory_payable(
            payable_id,
            self._id("layer"),
            ENTITY,
            self.period,
            self.period + due_offset,
            D(cents) / D(100),
            *ts(self.period),
        )

    @precondition(lambda state: bool(state._open_payables()))
    @rule(index=st.integers(), cents=st.integers(1, 10_000))
    def pay_payable_partial(self, index: int, cents: int) -> None:
        open_payables = self._open_payables()
        payable = open_payables[index % len(open_payables)]
        amount = min(
            D(cents) / D(100), payable.outstanding_amount, self.book.ledger.balances()["cash"]
        )
        if amount > 0:
            self.book.pay_payable(payable.invoice_id, amount, self.period, *ts(self.period))

    @precondition(
        lambda state: bool(state._open_payables()) and state.book.ledger.balances()["cash"] > 0
    )
    @rule(index=st.integers())
    def pay_payable_full(self, index: int) -> None:
        open_payables = self._open_payables()
        payable = open_payables[index % len(open_payables)]
        if payable.outstanding_amount <= self.book.ledger.balances()["cash"]:
            self.book.pay_payable(
                payable.invoice_id, payable.outstanding_amount, self.period, *ts(self.period)
            )

    @precondition(lambda state: state.book.ledger.balances()["inventory"] > 0)
    @rule(cents=st.integers(1, 20_000))
    def consume_inventory(self, cents: int) -> None:
        available = self.book.ledger.balances()["inventory"]
        self.book.consume_inventory(
            min(D(cents) / D(100), available), self.period, *ts(self.period)
        )

    @precondition(lambda state: state.book.ledger.balances()["cash"] > 100)
    @rule(
        cents=st.integers(1, 20_000),
        residual_cents=st.integers(0, 50),
        life=st.integers(1, 6),
    )
    def acquire_ppe(self, cents: int, residual_cents: int, life: int) -> None:
        gross = min(D(cents) / D(100), self.book.ledger.balances()["cash"])
        residual = min(D(residual_cents) / D(100), gross - D("0.01"))
        self.book.acquire_ppe_asset(
            self._id("asset"),
            ENTITY,
            self.period,
            gross,
            residual,
            life,
            *ts(self.period),
        )

    @precondition(
        lambda state: any(
            asset.placed_in_service_period <= state.period
            and asset._target_accumulation(state.period) > asset.accumulated_depreciation
            for asset in state.book.ppe_assets.values()
        )
    )
    @rule()
    def depreciate_ppe(self) -> None:
        self.book.depreciate_ppe(self.period, *ts(self.period))

    @rule(
        principal_cents=st.integers(100, 20_000),
        basis_points=st.integers(0, 2_000),
        life=st.integers(1, 8),
    )
    def borrow_debt(self, principal_cents: int, basis_points: int, life: int) -> None:
        self.book.borrow_debt(
            self._id("debt"),
            ENTITY,
            self.period,
            self.period + life,
            D(principal_cents) / D(100),
            D(basis_points) / D(10_000),
            *ts(self.period),
        )

    @precondition(
        lambda state: any(
            tranche.issued_period < state.period <= tranche.maturity_period
            and (tranche.tranche_id, state.period) not in state.book.accrued_interest
            and (
                state.period == tranche.issued_period + 1
                or (tranche.tranche_id, state.period - 1) in state.book.accrued_interest
            )
            for tranche in state.book.debt_tranches.values()
        )
    )
    @rule(index=st.integers())
    def accrue_interest(self, index: int) -> None:
        eligible = [
            tranche
            for tranche in self.book.debt_tranches.values()
            if tranche.issued_period < self.period <= tranche.maturity_period
            and (tranche.tranche_id, self.period) not in self.book.accrued_interest
            and (
                self.period == tranche.issued_period + 1
                or (tranche.tranche_id, self.period - 1) in self.book.accrued_interest
            )
        ]
        tranche = eligible[index % len(eligible)]
        self.book.accrue_interest(tranche.tranche_id, self.period, *ts(self.period))

    @precondition(
        lambda state: (
            state.book.ledger.balances()["interest_payable"] > 0
            and state.book.ledger.balances()["cash"] > 0
        )
    )
    @rule(cents=st.integers(1, 5_000))
    def pay_accrued_interest_partial(self, cents: int) -> None:
        amount = min(
            D(cents) / D(100),
            self.book.ledger.balances()["interest_payable"],
            self.book.ledger.balances()["cash"],
        )
        self.book.pay_accrued_interest(amount, self.period, *ts(self.period))

    @precondition(
        lambda state: bool(state_tranches(state)) and state.book.ledger.balances()["cash"] > 0
    )
    @rule(index=st.integers(), cents=st.integers(1, 20_000))
    def repay_debt_partial(self, index: int, cents: int) -> None:
        tranches = list(state_tranches(self))
        tranche = tranches[index % len(tranches)]
        amount = min(
            D(cents) / D(100),
            tranche.principal_outstanding,
            self.book.ledger.balances()["cash"],
        )
        if amount > 0:
            self.book.repay_debt(tranche.tranche_id, amount, self.period, *ts(self.period))

    @precondition(
        lambda state: any(
            tranche.principal_outstanding <= state.book.ledger.balances()["cash"]
            for tranche in state_tranches(state)
        )
    )
    @rule(index=st.integers())
    def repay_debt_full(self, index: int) -> None:
        cash = self.book.ledger.balances()["cash"]
        eligible = [
            tranche for tranche in state_tranches(self) if tranche.principal_outstanding <= cash
        ]
        tranche = eligible[index % len(eligible)]
        self.book.repay_debt(
            tranche.tranche_id, tranche.principal_outstanding, self.period, *ts(self.period)
        )

    @precondition(lambda state: state.period < 10)
    @rule()
    def advance_period(self) -> None:
        self.book.ledger.close(self.period, f"2025-{self.period:02d}-28T00:00:00Z")
        self.period += 1

    @invariant()
    def all_accounting_invariants_hold(self) -> None:
        balances = self.book.ledger.balances()
        assert self.book.validate_reconciliations() == set()
        assert (
            sum(item.outstanding_amount for item in self.book.receivables.values())
            == balances["ar"]
        )
        assert (
            sum(item.outstanding_amount for item in self.book.payables.values()) == balances["ap"]
        )
        assert (
            sum(item.remaining_cost for item in self.book.inventory_layers.values())
            == balances["inventory"]
        )
        assert sum(item.gross_cost for item in self.book.ppe_assets.values()) == balances["ppe"]
        assert (
            sum(item.accumulated_depreciation for item in self.book.ppe_assets.values())
            == balances["acc_dep"]
        )
        assert (
            sum(item.principal_outstanding for item in self.book.debt_tranches.values())
            == balances["debt"]
        )
        assert (
            sum(item.unpaid_amount for item in self.book.accrued_interest.values())
            == balances["interest_payable"]
        )
        assert balances["cash"] >= 0
        assert balances["ar"] >= 0
        assert balances["inventory"] >= 0
        assert balances["ap"] >= 0
        assert balances["debt"] >= 0
        assert balances["interest_payable"] >= 0
        balance_sheet = L.balance_sheet(self.book.ledger, self.period)
        assert (
            balance_sheet["total_assets"]
            == balance_sheet["total_liabilities"] + balance_sheet["total_equity"]
        )
        assert L.cash_flow_direct(self.book.ledger, self.period) == L.cash_flow_indirect(
            self.book.ledger, self.period
        )


def state_tranches(state: OperationalBookMachine) -> list[S.DebtTranche]:
    return [
        tranche
        for tranche in state.book.debt_tranches.values()
        if tranche.principal_outstanding > 0
    ]


TestOperationalBookMachine = OperationalBookMachine.TestCase
TestOperationalBookMachine.settings = machine_settings(
    max_examples=50,
    stateful_step_count=35,
)
