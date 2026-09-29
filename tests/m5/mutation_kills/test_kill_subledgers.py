"""M5.3 killing tests for :mod:`fwf_kernel.subledgers`.

The canonical FWF kernel suite builds subledger records only through
:class:`OperationalBook`'s own mutators, so every record it ever sees is one the
book produced and immediately reconciled against the control accounts. That
leaves two whole surfaces unobserved:

* the *record* invariants, which the book never violates because it constructs
  records correctly in the first place. Nothing checked that a zero-value
  invoice, a residual value equal to gross cost, or a tranche maturing in the
  period it was issued is actually rejected -- the book simply never tried.
* the five **control reconciliations**, which compare the sum of the subledger
  records against the corresponding ledger control account. Those are
  cross-object checks, so unlike the per-record re-validation in
  :meth:`OperationalBook.validate_reconciliations` they are *not* redundant with
  construction, and they are the ones an accounting system actually rests on: a
  receivable subledger that disagrees with AR is a broken book even when every
  individual invoice is well formed.

The two are separated deliberately here. The record tests assert the boundary
where the invariant is enforced. The reconciliation tests build a book whose
records are individually perfect but collectively inconsistent with the ledger,
which is the only way to reach those branches.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from fwf_kernel import ledger as L
from fwf_kernel import subledgers as S

D = Decimal
ENTITY = "ENT-KILL-SUBLEDGERS"
OTHER = "ENT-SOMEONE-ELSE"
TS = "2025-03-01T00:00:00Z"
ZERO = S.ZERO


def raises_value(expected: str, call: Callable[[], object]) -> None:
    """Assert ``call`` raises ``ValueError`` whose message is exactly ``expected``."""
    with pytest.raises(ValueError) as info:
        call()
    assert str(info.value) == expected, f"message {str(info.value)!r} is not {expected!r}"


def book(entries: list[L.JournalEntry] | None = None) -> L.Ledger:
    led = L.Ledger(ENTITY)
    for entry_obj in entries or []:
        led.post(entry_obj)
    return led


def opening(
    cash: str = "10000.00", credit: str = "10000.00", account: str = "common_stock"
) -> L.JournalEntry:
    return L.make("open-0", ENTITY, 0, "opening", {"cash": cash}, {account: credit}, TS, TS)


def invoice(
    invoice_id: str = "AR-1",
    *,
    entity_id: str = ENTITY,
    issued_period: int = 0,
    due_period: int = 1,
    original: str = "1000.00",
    outstanding: str = "1000.00",
) -> S.ReceivableInvoice:
    return S.ReceivableInvoice(
        invoice_id=invoice_id,
        entity_id=entity_id,
        issued_period=issued_period,
        due_period=due_period,
        original_amount=D(original),
        _outstanding_amount=D(outstanding),
    )


def payable(
    invoice_id: str = "AP-1",
    *,
    entity_id: str = ENTITY,
    issued_period: int = 0,
    due_period: int = 1,
    original: str = "400.00",
    outstanding: str = "400.00",
) -> S.PayableInvoice:
    return S.PayableInvoice(
        invoice_id=invoice_id,
        entity_id=entity_id,
        issued_period=issued_period,
        due_period=due_period,
        original_amount=D(original),
        _outstanding_amount=D(outstanding),
    )


def layer(
    layer_id: str = "L-1",
    *,
    acquired_period: int = 0,
    original: str = "600.00",
    remaining: str = "600.00",
) -> S.InventoryLayer:
    return S.InventoryLayer(
        layer_id=layer_id,
        acquired_period=acquired_period,
        original_cost=D(original),
        _remaining_cost=D(remaining),
    )


def asset(
    asset_id: str = "PPE-1",
    *,
    entity_id: str = ENTITY,
    placed_in_service_period: int = 0,
    gross: str = "1000.00",
    residual: str = "100.00",
    life: int = 5,
    accumulated: str = "0.00",
) -> S.PPEAsset:
    return S.PPEAsset(
        asset_id=asset_id,
        entity_id=entity_id,
        placed_in_service_period=placed_in_service_period,
        gross_cost=D(gross),
        residual_value=D(residual),
        useful_life_periods=life,
        _accumulated_depreciation=D(accumulated),
    )


def tranche(
    tranche_id: str = "T-1",
    *,
    entity_id: str = ENTITY,
    issued_period: int = 0,
    maturity_period: int = 4,
    original: str = "10000.00",
    outstanding: str = "10000.00",
    rate: str = "0.04",
) -> S.DebtTranche:
    return S.DebtTranche(
        tranche_id=tranche_id,
        entity_id=entity_id,
        issued_period=issued_period,
        maturity_period=maturity_period,
        original_principal=D(original),
        _principal_outstanding=D(outstanding),
        annual_rate=D(rate),
    )


# ================================================= receivable / payable records
def test_receivable_rejects_a_zero_original_amount() -> None:
    """Kills ``ReceivableInvoice.__post_init__`` mutants 31, 33 and 34.

    The ``allow_zero=False`` on the original amount is the only thing separating a
    real receivable from a zero-value placeholder. Dropping the flag, replacing it
    with the default, or flipping it to ``True`` all let a zero invoice into the
    subledger, where it contributes nothing to AR while still occupying a line.
    """
    raises_value(
        "amount must be positive",
        lambda: invoice(original="0.00"),
    )
    raises_value(
        "amount must be positive",
        lambda: payable(original="0.00"),
    )


def test_payable_rejects_a_zero_original_amount() -> None:
    """The same boundary on the payable side, which is a separate code path."""
    raises_value(
        "amount must be positive",
        lambda: payable(invoice_id="AP-2", original="0.00"),
    )


def test_invoice_rejects_a_negative_outstanding_amount() -> None:
    """Kills the ``money`` seed-drop mutants on the outstanding amount.

    A negative outstanding balance would *increase* AR rather than reduce it, so
    the AR control account would still tie while the aging report showed a
    receivable nobody owes.
    """
    raises_value(
        "amount must be non-negative",
        lambda: invoice(outstanding="-1.00"),
    )


def test_invoice_rejects_an_outstanding_amount_above_its_original() -> None:
    """Kills the ``outstanding > original`` strictness mutants.

    Overstating what is outstanding against what was billed is the one error an
    AR subledger exists to prevent, and the bound is inclusive: equal is the
    legitimate fully-unpaid case, above is not.
    """
    raises_value(
        "outstanding_amount cannot exceed original_amount",
        lambda: invoice(outstanding="1000.01"),
    )
    # Equal is fine, which is what makes the bound strict rather than inclusive.
    assert invoice(original="1000.00", outstanding="1000.00").outstanding_amount == D("1000.00")


def test_invoice_rejects_a_due_period_before_its_issued_period() -> None:
    """Kills the ``due_period >= issued_period`` mutants on both invoice types.

    An invoice that falls due before it was issued is not payable, so the aging
    report would place it in a bucket that ends before its own issue date.
    """
    raises_value(
        "due_period must be >= issued_period",
        lambda: invoice(issued_period=2, due_period=1),
    )
    raises_value(
        "due_period must be >= issued_period",
        lambda: payable(issued_period=2, due_period=1),
    )


def test_invoice_rejects_a_non_record_period() -> None:
    """Kills the ``_record_period`` guard on both invoice period fields.

    ``bool`` is an ``int`` in Python, so period ``True`` would silently become
    period 1 without the explicit guard, and a negative period would sort before
    the opening balance in every aging bucket.
    """
    raises_value(
        "issued_period must be a record period >= 0",
        lambda: invoice(issued_period=True),
    )
    raises_value(
        "due_period must be a record period >= 0",
        lambda: invoice(due_period=-1),
    )


# ============================================================ inventory record
def test_inventory_layer_rejects_a_zero_original_cost() -> None:
    """Kills the ``allow_zero=False`` mutants on :class:`InventoryLayer`."""
    raises_value("amount must be positive", lambda: layer(original="0.00"))


def test_inventory_layer_rejects_remaining_above_original() -> None:
    """Kills the ``remaining > original`` strictness mutants.

    Remaining cost is what is left to sell; if it exceeds what the layer cost, the
    FIFO plan would issue more cost than was ever paid for the layer and the
    inventory control account would drift upward.
    """
    raises_value(
        "remaining_cost cannot exceed original_cost",
        lambda: layer(original="600.00", remaining="600.01"),
    )


# ============================================================ PP&E record
def test_ppe_asset_rejects_a_zero_gross_cost() -> None:
    """Kills the ``allow_zero=False`` mutants on :class:`PPEAsset`'s gross cost.

    A zero-cost asset still occupies a depreciation schedule, so accepting one
    gives the book an asset with no cost that nonetheless rolls forward.
    """
    raises_value("amount must be positive", lambda: asset(gross="0.00"))


def test_ppe_asset_rejects_a_residual_value_equal_to_gross_cost() -> None:
    """Kills the ``residual_value >= gross_cost`` mutants.

    The bound is strict: a residual equal to gross cost leaves nothing to
    depreciate, so the asset's whole life would produce zero depreciation while
    still appearing on the schedule. This is the classic off-by-one in a
    depreciation policy, and it is invisible in the totals.
    """
    raises_value(
        "residual_value must be less than gross_cost",
        lambda: asset(gross="1000.00", residual="1000.00"),
    )
    # One cent below is accepted, which is what makes the bound strict.
    assert asset(gross="1000.00", residual="999.99").residual_value == D("999.99")


def test_ppe_asset_rejects_accumulated_depreciation_above_the_depreciable_amount() -> None:
    """Kills the ``accumulated > gross - residual`` mutants.

    Depreciation may never exceed cost less residual. Widening the bound to
    ``gross + residual`` lets an asset be depreciated below its residual value,
    which is exactly the error the accumulated-depreciation control account
    exists to catch.
    """
    raises_value(
        "accumulated_depreciation exceeds the depreciable amount",
        lambda: asset(gross="1000.00", residual="100.00", accumulated="900.01"),
    )
    # Exactly the depreciable amount is the legitimate fully-depreciated state.
    assert asset(gross="1000.00", residual="100.00", accumulated="900.00") is not None


def test_ppe_asset_rejects_a_non_positive_useful_life() -> None:
    """Kills the ``useful_life_periods > 0`` mutants, including the bool case.

    ``True`` is an ``int``, so a useful life of ``True`` would compute as a
    one-period life -- full depreciation in year one -- without the explicit
    ``isinstance`` guard.
    """
    raises_value("useful_life_periods must be > 0", lambda: asset(life=0))
    raises_value("useful_life_periods must be > 0", lambda: asset(life=True))


# ============================================================== debt record
def test_debt_tranche_rejects_maturity_in_its_issue_period() -> None:
    """Kills the ``maturity_period <= issued_period`` mutants.

    A tranche that matures in the period it was issued has no life, yet would
    still contribute its principal to the debt control account and earn a full
    quarter of interest on a zero-day loan.
    """
    raises_value(
        "maturity_period must be > issued_period",
        lambda: tranche(issued_period=0, maturity_period=0),
    )


def test_debt_tranche_rejects_a_zero_original_principal() -> None:
    """Kills the ``allow_zero=False`` mutants on :class:`DebtTranche`."""
    raises_value("amount must be positive", lambda: tranche(original="0.00"))


def test_debt_tranche_rejects_principal_outstanding_above_its_original() -> None:
    """Kills the ``principal_outstanding > original_principal`` mutants.

    Outstanding principal above the original would put the debt control account
    above the amount actually borrowed, with no corresponding cash ever having
    arrived.
    """
    raises_value(
        "principal_outstanding cannot exceed original_principal",
        lambda: tranche(original="10000.00", outstanding="10000.01"),
    )


# ================================================== control reconciliations
#: Ledger and records that agree on every control account. Each test below takes
#: this pair and perturbs exactly one figure, so the only rule that can fire is
#: the one under test. Perturbing a second account at once would leave the
#: expected failure set ambiguous and the test would stop proving anything.
_AR = "1000.00"
_AP = "600.00"
_GROSS = "1000.00"
_ACCUMULATED = "50.00"
_PRINCIPAL = "10000.00"


def coherent() -> tuple[L.Ledger, dict[str, object]]:
    """A ledger and a full set of records that reconcile on all five rules."""
    led = book([opening()])
    led.post(L.event("ar-1", ENTITY, 1, "sale_on_credit", _AR, TS, TS))
    led.post(L.event("ap-1", ENTITY, 1, "buy_inventory_on_credit", _AP, TS, TS))
    led.post(L.event("capex-1", ENTITY, 1, "capex", _GROSS, TS, TS))
    led.post(L.event("dep-1", ENTITY, 1, "depreciate", _ACCUMULATED, TS, TS))
    led.post(L.event("borrow-1", ENTITY, 1, "borrow", _PRINCIPAL, TS, TS))
    return led, {
        "receivables": [invoice(original=_AR, outstanding=_AR)],
        "payables": [payable(original=_AP, outstanding=_AP)],
        "inventory_layers": [layer(original=_AP, remaining=_AP)],
        "ppe_assets": [asset(gross=_GROSS, residual="0.00", life=10, accumulated=_ACCUMULATED)],
        "debt_tranches": [tranche(original=_PRINCIPAL, outstanding=_PRINCIPAL)],
    }


def perturbed(**changes: object) -> object:
    """``coherent()``'s records with named record collections replaced."""
    _, records = coherent()
    return {**records, **changes}


def rule(*ids: str) -> str:
    """The exact ``_require_valid`` message for a set of failed rule ids."""
    return f"invalid operational book: {sorted(ids)}"


def test_a_coherent_book_reports_no_broken_reconciliations() -> None:
    """The negative case, and the precondition for every test below.

    Without this, a rule that fired unconditionally would satisfy each
    perturbation test on its own.
    """
    led, records = coherent()

    op = S.OperationalBook(led, **records)  # type: ignore[arg-type]

    assert op.validate_reconciliations() == set()
    assert ZERO == D("0.00")


def test_receivable_total_must_tie_to_the_ar_control_account() -> None:
    """Kills the ``receivable control mismatch`` mutants and the AR rule.

    A receivable subledger whose invoices total more than the AR control account
    is the defining broken-AR state: every invoice is individually well formed,
    the ledger is internally balanced, and the two still disagree. Only a
    cross-object comparison finds it, which is what this rule is.
    """
    led, _ = coherent()

    raises_value(
        rule(S.AR_RULE),
        lambda: S.OperationalBook(
            led,
            **perturbed(receivables=[invoice(original="1200.00", outstanding="1200.00")]),
        ),
    )


def test_payable_total_must_tie_to_the_ap_control_account() -> None:
    """Kills the ``payable control mismatch`` mutants and the AP rule."""
    led, _ = coherent()

    raises_value(
        rule(S.AP_RULE),
        lambda: S.OperationalBook(
            led,
            **perturbed(payables=[payable(original="700.00", outstanding="700.00")]),
        ),
    )


def test_inventory_total_must_tie_to_the_inventory_control_account() -> None:
    """Kills the ``inventory control mismatch`` mutants and the FIFO rule.

    The layer keeps its original cost and gives up part of it, so the inventory
    account still holds the full purchase price while the layer says otherwise --
    the state in which FIFO would issue cost the books no longer carry.
    """
    led, _ = coherent()

    raises_value(
        rule(S.INVENTORY_RULE),
        lambda: S.OperationalBook(
            led,
            **perturbed(inventory_layers=[layer(original=_AP, remaining="500.00")]),
        ),
    )


def test_ppe_gross_cost_must_tie_to_the_ppe_control_account() -> None:
    """Kills the first half of the ``PP&E control mismatch`` mutants.

    A schedule whose gross cost disagrees with the PP&E account would depreciate
    an asset the balance sheet does not carry at the amount the schedule claims.
    """
    led, _ = coherent()

    raises_value(
        rule(S.PPE_RULE),
        lambda: S.OperationalBook(
            led,
            **perturbed(
                ppe_assets=[asset(gross="900.00", residual="0.00", life=10, accumulated=_ACCUMULATED)]
            ),
        ),
    )


def test_ppe_accumulated_depreciation_must_tie_to_its_control_account() -> None:
    """Kills the second half of the ``PP&E control mismatch`` mutants.

    The rule checks gross cost and accumulated depreciation against two separate
    accounts, so the second half needs its own case: an asset whose accumulated
    depreciation has run ahead of the accumulated-depreciation account.
    """
    led, _ = coherent()

    raises_value(
        rule(S.PPE_RULE),
        lambda: S.OperationalBook(
            led,
            **perturbed(ppe_assets=[asset(gross=_GROSS, residual="0.00", life=10, accumulated="60.00")]),
        ),
    )


def test_debt_principal_must_tie_to_the_debt_control_account() -> None:
    """Kills the ``debt or interest-payable control mismatch`` mutants.

    The rule compares the sum of tranche principals against the debt control
    account. A book that reports debt it has not booked -- or has stopped
    carrying debt it still owes on -- is the state this exists to catch.
    """
    led, _ = coherent()

    raises_value(
        rule(S.DEBT_RULE),
        lambda: S.OperationalBook(
            led,
            **perturbed(debt_tranches=[tranche(original=_PRINCIPAL, outstanding="9000.00")]),
        ),
    )


# ============================================================ aging buckets
def test_aging_buckets_accumulate_every_invoice_in_them() -> None:
    """Kills the ``+=`` -> ``=`` / ``-=`` mutants in :func:`invoice_aging`.

    Aging is a total per bucket, not the last invoice that happened to land in
    it. Replacing the accumulation with an assignment reports one invoice's
    balance as the bucket's, and negating it hides the debt entirely -- so three
    invoices in the same bucket must sum, and each of the three non-current
    branches is exercised with more than one invoice.
    """
    invoices = {
        "AR-1": invoice("AR-1", due_period=0, original="100.00", outstanding="100.00"),
        "AR-2": invoice("AR-2", due_period=0, original="200.00", outstanding="200.00"),
        "AR-3": invoice("AR-3", due_period=0, original="300.00", outstanding="300.00"),
    }

    buckets = S.invoice_aging(invoices, 3)

    assert buckets == {
        "current": D("0.00"),
        "1-period-past-due": D("0.00"),
        "2-periods-past-due": D("0.00"),
        "3+-periods-past-due": D("600.00"),
    }


def test_aging_buckets_separate_the_four_ages() -> None:
    """Kills the per-bucket boundary mutants with two invoices per bucket.

    Each bucket accumulates separately, so a second invoice in every bucket is
    what distinguishes "summed" from "overwritten", and the distinct totals
    distinguish the boundaries themselves.
    """

    def aged(name: str, due: int, amount: str) -> S.ReceivableInvoice:
        return invoice(name, issued_period=0, due_period=due, original=amount, outstanding=amount)

    buckets = S.invoice_aging(
        {
            "A": aged("A", 3, "10.00"),
            "B": aged("B", 3, "20.00"),
            "C": aged("C", 2, "30.00"),
            "D": aged("D", 2, "40.00"),
            "E": aged("E", 1, "50.00"),
            "F": aged("F", 1, "60.00"),
            "G": aged("G", 0, "70.00"),
            "H": aged("H", 0, "80.00"),
        },
        3,
    )

    assert buckets == {
        "current": D("30.00"),
        "1-period-past-due": D("70.00"),
        "2-periods-past-due": D("110.00"),
        "3+-periods-past-due": D("150.00"),
    }


def test_aging_uses_the_outstanding_balance_not_the_original() -> None:
    """Kills the ``invoice.outstanding_amount`` -> ``original_amount`` mutants.

    A partly-collected invoice ages at what is still owed. Reading the original
    amount instead would overstate every aged bucket, which is the difference
    between a receivable that is collectable and one that is not.
    """
    partial = S.ReceivableInvoice("AR-1", ENTITY, 0, 0, D("500.00"), D("120.00"))

    assert S.invoice_aging({"AR-1": partial}, 3)["3+-periods-past-due"] == D("120.00")
    assert S.invoice_aging({"AR-1": partial}, 3)["current"] == D("0.00")


# ==================================================== record-copying guards
def test_a_detail_record_must_carry_an_id() -> None:
    """Kills the ``getattr``/id-guard mutants in :func:`_copy_records`.

    The record is identified by the field the book reconciles it under, so a
    record that is not of the expected type, or one that does not carry that
    field, has to be refused before it is filed under a key the caller never
    chose. Both spellings of the input -- a mapping and a bare sequence -- have
    to reach the same guards.
    """
    with pytest.raises(TypeError, match="expected ReceivableInvoice detail record"):
        S._copy_records({"AR-1": object()}, S.ReceivableInvoice, "invoice_id")
    with pytest.raises(TypeError, match="expected ReceivableInvoice detail record"):
        S._copy_records([object()], S.ReceivableInvoice, "invoice_id")
    # A field the record does not have is refused rather than filed under None.
    raises_value(
        "no_such_field must be a non-empty string",
        lambda: S._copy_records([invoice("AR-1")], S.ReceivableInvoice, "no_such_field"),
    )


def test_a_detail_mapping_key_must_name_the_record_it_holds() -> None:
    """Kills the ``key is not None and key != record_id`` mutants.

    Keying a receivable by one id while the record carries another would let the
    subledger report a balance under an id the caller never posted, so the key
    has to agree with the record and duplicates have to be refused.
    """
    raises_value(
        "detail mapping key does not match invoice_id",
        lambda: S._copy_records({"AR-OTHER": invoice("AR-1")}, S.ReceivableInvoice, "invoice_id"),
    )
    raises_value(
        "duplicate detail record AR-1",
        lambda: S._copy_records(
            [invoice("AR-1"), invoice("AR-1", original="200.00", outstanding="200.00")],
            S.ReceivableInvoice,
            "invoice_id",
        ),
    )


# ==================================================== shared value validators
def test_a_non_string_value_is_refused_before_it_is_inspected() -> None:
    """Kills the ``_text`` ``or`` -> ``and`` mutants.

    A non-string has no ``.strip``, so folding the two conditions into an ``and``
    turns a clear rejection into an ``AttributeError`` raised from inside the
    validator -- and, for a value that *does* expose ``strip``, into silent
    acceptance. The refusal has to be a ``ValueError`` about the value.
    """
    raises_value(
        "entity_id must be a non-empty string",
        lambda: S._text(123, "entity_id"),
    )
    raises_value(
        "entity_id must be a non-empty string",
        lambda: S._text("   ", "entity_id"),
    )


def test_a_non_integer_period_is_refused_before_it_is_compared() -> None:
    """Kills the ``_period`` ``or`` -> ``and`` mutants.

    A bool is an ``int`` in Python and a string is not an ``int`` at all, so each
    of the three clauses has to be able to reject on its own: booleans are not
    period numbers, non-integers are not period numbers, and neither is zero.
    """
    raises_value("period must be an operating period >= 1", lambda: S._period(True, "period"))
    raises_value("period must be an operating period >= 1", lambda: S._period("1", "period"))
    raises_value("period must be an operating period >= 1", lambda: S._period(0, "period"))
    assert S._period(1, "period") is None


def test_a_zero_amount_cannot_be_posted_to_an_operational_account() -> None:
    """Kills the ``amount <= ZERO`` -> ``amount < ZERO`` mutants in ``_post``.

    A zero-amount posting is not a no-op: it writes a journal entry, advances the
    entry sequence and leaves a line pair on the ledger. The bound is therefore
    strict, and the rejection has to happen before the entry is posted.
    """
    led, records = coherent()
    op = S.OperationalBook(led, **records)  # type: ignore[arg-type]
    before = len(op.ledger.entries)

    with pytest.raises(ValueError, match="a posted operational amount must be positive"):
        op._post("sale_on_credit", ZERO, 1, TS, TS)

    assert len(op.ledger.entries) == before
    assert op._last_entry_sequence() == 0


# ================================================== boundary-value reconciliation
def _debt_ledger(accrued: str = "0.00") -> L.Ledger:
    """A ledger whose debt and interest-payable controls match a T-1 tranche."""
    led = book([opening(cash="20000.00", credit="20000.00")])
    led.post(L.event("borrow-1", ENTITY, 1, "borrow", _PRINCIPAL, TS, TS))
    if D(accrued):
        led.post(L.event("accrue-1", ENTITY, 2, "accrue_interest", accrued, TS, TS))
    return led


def _int_book(
    schedules: list[S.AccruedInterestSchedule], *, issued: int = 0, maturity: int = 6, accrued: str = "0.00"
) -> S.OperationalBook:
    return S.OperationalBook(
        _debt_ledger(accrued),
        debt_tranches=[tranche(issued_period=issued, maturity_period=maturity)],
        accrued_interest=schedules,
    )


def _q(period: int, opening: str, accrued: str) -> S.AccruedInterestSchedule:
    return S.AccruedInterestSchedule("T-1", period, D(opening), D(accrued), D("0.00"), D(_PRINCIPAL))


def test_an_interest_schedule_gap_is_rejected_rather_than_reseeded() -> None:
    """Kills the ``period == issued_period + 1 and prior is None`` mutants.

    The first schedule of a tranche opens at the original principal; every later
    one opens at the prior quarter's ending principal. Folding the first clause
    into an ``or`` lets a mid-life schedule with a missing predecessor open at
    the *original* principal, silently re-running interest on money that may
    already have been repaid. A gap at periods 2-3 followed by a schedule whose
    opening and accrued figures are internally consistent is exactly the state
    the original rejects and the mutant accepts.
    """
    raises_value(
        rule(S.DEBT_RULE),
        lambda: _int_book([_q(1, _PRINCIPAL, "100.00"), _q(4, _PRINCIPAL, "100.00")]),
    )


def test_a_later_schedule_must_carry_its_predecessors_ending_principal() -> None:
    """Kills the ``opening_principal != expected_opening`` mutants.

    After the first quarter a schedule's opening principal has to be carried
    from its predecessor's ending balance. A schedule that opens somewhere else
    has no source for that number, and the mutated guard that flips the
    comparison would reconcile against an invented balance -- so the stale
    schedule's accrued amount is made consistent with its (wrong) opening, which
    is what lets the mutant reach the ledger instead of tripping the formula.
    """
    raises_value(
        rule(S.DEBT_RULE),
        lambda: _int_book([_q(1, _PRINCIPAL, "100.00"), _q(2, "9000.00", "90.00")], accrued="90.00"),
    )


def test_the_first_schedule_must_open_at_the_original_principal() -> None:
    """Kills the mutants on the first-quarter opening derivation.

    The very first quarter's schedule has a defined opening -- the original
    principal -- and a schedule that opens somewhere else has no source for that
    number. As above, the stale figure carries a self-consistent accrued amount
    so the mutant under test is the comparison and not the formula.
    """
    raises_value(
        rule(S.DEBT_RULE),
        lambda: _int_book([_q(1, "9000.00", "90.00")], accrued="90.00"),
    )


def test_two_consecutive_schedules_reconcile() -> None:
    """The negative control for the contiguity guards.

    Without this, a contiguity check that rejected every schedule would satisfy
    each rejection test above on its own. Two internally consistent quarters,
    with the ledger's interest-payable control matching their unpaid accruals,
    must reconcile cleanly.
    """
    book = _int_book([_q(1, _PRINCIPAL, "100.00"), _q(2, _PRINCIPAL, "100.00")], accrued="200.00")

    assert book.validate_reconciliations() == set()


def test_zero_rate_debt_and_zero_accruals_reconcile() -> None:
    """Kills the ``annual_rate < ZERO`` and ``accrued_amount < ZERO`` mutants.

    Construction admits a zero-rate tranche and records a quarter with nothing
    accrued in it -- the schedule is part of the audit trail even when there is
    nothing to book. Relaxing either bound to ``<=`` would reject a book the
    mutators legitimately produce, so the zero states must reconcile.
    """
    led = _debt_ledger()
    op = S.OperationalBook(
        led,
        debt_tranches=[tranche(issued_period=0, maturity_period=4, rate="0.00")],
    )
    op.accrue_interest("T-1", 1, TS, TS)

    assert op.accrued_interest[("T-1", 1)].accrued_amount == ZERO
    assert op.validate_reconciliations() == set()


def test_a_fully_repaid_tranche_accrues_zero_opening_schedules_that_reconcile() -> None:
    """Kills the ``opening_principal < ZERO`` -> ``<= ZERO`` widening.

    Full repayment drives principal_outstanding to zero, the next accrual
    records that zero as its ending principal, and the quarter after it opens
    at zero. A zero opening is a legitimate audit-trail state -- it documents a
    quarter with nothing left to accrue on -- so the validator must accept it.
    Widening the bound to ``<=`` flags a book the public mutators themselves
    produce. Built through the public mutators only, so it is exactly the state
    the nightly machine can reach.
    """
    led = book([opening(cash="1000.00", credit="1000.00")])
    op = S.OperationalBook(led)
    op.borrow_debt("T-1", ENTITY, 1, 4, "100.00", "0.08", TS, TS)
    op.repay_debt("T-1", "100.00", 1, TS, TS)
    op.accrue_interest("T-1", 2, TS, TS)
    assert op.accrued_interest[("T-1", 2)].ending_principal == ZERO
    op.accrue_interest("T-1", 3, TS, TS)

    assert op.accrued_interest[("T-1", 3)].opening_principal == ZERO
    assert op.validate_reconciliations() == set()


def test_one_cent_balances_reconcile() -> None:
    """Kills the ``<= ZERO`` -> ``< ZERO`` mutants on cost and principal.

    The guards are strict in one direction only: a one-cent principal, cost or
    invoice is a real balance that must reconcile. Zero is refused by
    construction for these records, so the one-cent book is what separates the
    strict bound from an inclusive one.
    """
    led = book([opening(cash="20000.00", credit="20000.00")])
    led.post(L.event("ar-1", ENTITY, 1, "sale_on_credit", "0.01", TS, TS))
    led.post(L.event("ap-1", ENTITY, 1, "buy_inventory_on_credit", "0.01", TS, TS))
    led.post(L.event("capex-1", ENTITY, 1, "capex", "0.01", TS, TS))
    led.post(L.event("borrow-1", ENTITY, 1, "borrow", _PRINCIPAL, TS, TS))
    led.post(L.event("borrow-2", ENTITY, 1, "borrow", "0.01", TS, TS))
    thin = {
        "receivables": [invoice(original="0.01", outstanding="0.01")],
        "payables": [payable(original="0.01", outstanding="0.01")],
        "inventory_layers": [layer(original="0.01", remaining="0.01")],
        "ppe_assets": [asset(gross="0.01", residual="0.00", life=1, accumulated="0.00")],
        "debt_tranches": [
            tranche(original="0.01", outstanding="0.01"),
            tranche(tranche_id="T-2", original=_PRINCIPAL, outstanding=_PRINCIPAL),
        ],
    }

    op = S.OperationalBook(led, **thin)  # type: ignore[arg-type]

    assert op.validate_reconciliations() == set()


# ================================================== interest scheduling and PPE
def test_interest_accrues_only_inside_the_tranche_window() -> None:
    """Kills the ``issued_period < period <= maturity_period`` mutants.

    Accrual is bounded below by exclusivity -- the issuance quarter itself is
    not an accrual quarter -- and above by the maturity. Widening either end
    lets the book accrue interest outside the loan's life.
    """
    op = S.OperationalBook(_debt_ledger(), debt_tranches=[tranche(issued_period=1, maturity_period=4)])

    with pytest.raises(KeyError, match="unknown debt tranche NOPE"):
        op.accrue_interest("NOPE", 2, TS, TS)
    fresh = S.OperationalBook(_debt_ledger(), debt_tranches=[tranche(issued_period=1, maturity_period=4)])
    with pytest.raises(ValueError, match="interest must accrue after issue and no later than maturity"):
        fresh.accrue_interest("T-1", 1, TS, TS)
    fresh2 = S.OperationalBook(_debt_ledger(), debt_tranches=[tranche(issued_period=1, maturity_period=4)])
    with pytest.raises(ValueError, match="interest must accrue after issue and no later than maturity"):
        fresh2.accrue_interest("T-1", 5, TS, TS)
    fresh3 = S.OperationalBook(_debt_ledger(), debt_tranches=[tranche(issued_period=1, maturity_period=4)])
    assert fresh3.accrue_interest("T-1", 2, TS, TS) == D("100.00")


def test_interest_cannot_accrue_twice_in_the_same_quarter() -> None:
    """Kills the ``interest already accrued`` mutants.

    One accrual per tranche per quarter is the schedule contract: a second
    accrual for the same key would compound the quarter's interest and
    desynchronise every later quarter's opening principal.
    """
    op = S.OperationalBook(_debt_ledger(), debt_tranches=[tranche(issued_period=1, maturity_period=4)])
    op.accrue_interest("T-1", 2, TS, TS)

    raises_value(
        "interest already accrued for T-1 in period 2",
        lambda: op.accrue_interest("T-1", 2, TS, TS),
    )


def test_a_zero_rate_tranche_records_its_quarter_without_posting() -> None:
    """Kills the ``accrued > ZERO`` posting mutants.

    A zero-rate tranche still gets its schedule -- the record of a quarter with
    nothing accrued is part of the audit trail -- but posts no journal entry,
    because there is no amount to book. Dropping the guard would attempt a
    zero-amount posting, which the posting path refuses.
    """
    op = S.OperationalBook(
        _debt_ledger(), debt_tranches=[tranche(issued_period=1, maturity_period=4, rate="0.00")]
    )
    before = len(op.ledger.entries)

    accrued = op.accrue_interest("T-1", 2, TS, TS)

    assert accrued == ZERO
    assert len(op.ledger.entries) == before
    assert ("T-1", 2) in op.accrued_interest


def test_depreciation_skips_not_yet_placed_assets_without_stopping() -> None:
    """Kills the ``continue`` -> ``break`` mutant in the depreciation loop.

    A period before an asset was placed in service contributes nothing, and
    skipping it is not the same as stopping the loop: ``break`` would silently
    drop every *later* asset's depreciation, which is depreciation the ledger's
    schedule exists to book.
    """
    led = book([opening(cash="4000.00", credit="4000.00")])
    led.post(L.event("capex-1", ENTITY, 1, "capex", "2000.00", TS, TS))
    late = asset("PPE-LATE", placed_in_service_period=3, gross="1000.00", residual="0.00", life=10)
    early = asset("PPE-EARLY", placed_in_service_period=0, gross="1000.00", residual="0.00", life=10)
    op = S.OperationalBook(led, ppe_assets=[late, early])

    total = op.depreciate_ppe(1, TS, TS)

    assert total == D("100.00")
    assert op.ppe_assets["PPE-EARLY"].accumulated_depreciation == D("100.00")
    assert op.ppe_assets["PPE-LATE"].accumulated_depreciation == D("0.00")


def test_paying_out_cash_down_to_the_last_cent_is_allowed() -> None:
    """Kills the ``balances()['cash'] < amount`` mutants.

    The bound is strict: spending the last cent is allowed, spending a cent more
    than exists is not. An inclusive comparison would refuse the exact-balance
    payment, which is a legitimate operation.
    """
    led = book([opening(cash="10.00", credit="10.00")])
    op = S.OperationalBook(led)

    op._require_cash(D("10.00"))
    raises_value("insufficient cash for operation", lambda: op._require_cash(D("10.01")))


def test_paying_unaccrued_interest_is_refused() -> None:
    """Kills the ``payment exceeds accrued unpaid interest`` mutants.

    Interest can only be paid once it has been accrued and remains unpaid, and
    the payment is capped at exactly that pool. A guard that ignored the cap
    would let the book pay interest it never booked, which is where the
    interest-payable control account and the schedule stop agreeing.
    """
    op = S.OperationalBook(_debt_ledger(), debt_tranches=[tranche(issued_period=1, maturity_period=4)])
    op.accrue_interest("T-1", 2, TS, TS)

    with pytest.raises(ValueError, match="payment exceeds accrued unpaid interest"):
        op.pay_accrued_interest(D("100.01"), 2, TS, TS)
    paid = op.pay_accrued_interest(D("100.00"), 2, TS, TS)
    assert paid == D("100.00")
    assert op.accrued_interest[("T-1", 2)].unpaid_amount == ZERO


# ================================================== duplicates and zero amounts
def test_mutators_refuse_duplicates_in_their_own_collection() -> None:
    """Kills the ``duplicate ...`` mutants in every collection-owning mutator.

    Each record id is the key its subledger is reconciled under, so a mutator
    that overwrote an existing entry would silently retire a record the ledger
    still carries -- the receivable would still be in AR while its control
    record described the replacement. Every collection has its own guard, and
    each is provoked with its own id.
    """
    op = S.OperationalBook(coherent()[0], **coherent()[1])  # type: ignore[arg-type]

    raises_value(
        "duplicate receivable invoice AR-1", lambda: op.issue_receivable("AR-1", ENTITY, 1, 2, D(_AR), TS, TS)
    )
    raises_value(
        "duplicate payable invoice AP-1",
        lambda: op.issue_inventory_payable("AP-1", "L-NEW", ENTITY, 1, 2, D(_AP), TS, TS),
    )
    raises_value(
        "duplicate inventory layer L-1",
        lambda: op.issue_inventory_payable("AP-2", "L-1", ENTITY, 1, 2, D(_AP), TS, TS),
    )
    raises_value(
        "duplicate PP&E asset PPE-1",
        lambda: op.acquire_ppe_asset("PPE-1", ENTITY, 2, "100.00", "0.00", 5, TS, TS),
    )
    raises_value(
        "duplicate debt tranche T-1", lambda: op.borrow_debt("T-1", ENTITY, 2, 6, D("500.00"), "0.04", TS, TS)
    )


def test_operational_mutators_refuse_a_zero_amount() -> None:
    """Kills the ``allow_zero=False`` -> ``True``/removed mutants.

    Every operational amount writes a journal entry and advances the entry
    sequence, so zero is not a no-op but a phantom posting. The ``allow_zero``
    flag has to stay off at every call site -- flipping it to ``True`` or
    dropping the keyword entirely both admit the zero posting.
    """
    led, records = coherent()
    op = S.OperationalBook(led, **records)  # type: ignore[arg-type]
    zero = ZERO
    cases = [
        lambda: op.issue_receivable("AR-Z", ENTITY, 1, 2, zero, TS, TS),
        lambda: op.collect_receivable("AR-1", zero, 1, TS, TS),
        lambda: op.issue_inventory_payable("AP-Z", "L-Z", ENTITY, 1, 2, zero, TS, TS),
        lambda: op.pay_payable("AP-1", zero, 1, TS, TS),
        lambda: op.consume_inventory(zero, 1, TS, TS),
        lambda: op.acquire_ppe_asset("PPE-Z", ENTITY, 1, zero, "0.00", 5, TS, TS),
        lambda: op.borrow_debt("T-Z", ENTITY, 1, 5, zero, "0.04", TS, TS),
        lambda: op.repay_debt("T-1", zero, 1, TS, TS),
        lambda: op.pay_accrued_interest(zero, 1, TS, TS),
    ]
    for case in cases:
        with pytest.raises(ValueError, match="amount must be positive"):
            case()


def test_collecting_or_paying_more_than_outstanding_is_refused() -> None:
    """Kills the ``exceeds outstanding`` mutants on the collection mutators.

    A collection or payment is capped by the record's outstanding balance, and
    the cap is what keeps the subledger total in sync with the control account.
    Over-collecting would drive an invoice negative and break the tie-out in the
    same breath.
    """
    led, records = coherent()
    op = S.OperationalBook(led, **records)  # type: ignore[arg-type]

    raises_value(
        "collection exceeds outstanding receivable",
        lambda: op.collect_receivable("AR-1", D("1000.01"), 1, TS, TS),
    )
    raises_value(
        "payment exceeds outstanding payable",
        lambda: op.pay_payable("AP-1", D("600.01"), 1, TS, TS),
    )
    raises_value(
        "repayment exceeds outstanding principal",
        lambda: op.repay_debt("T-1", D("10000.01"), 1, TS, TS),
    )


def test_unknown_record_ids_are_refused_by_every_mutator_that_takes_one() -> None:
    """Kills the ``unknown ...`` KeyError mutants.

    A mutator aimed at an id the book does not hold has to be refused outright;
    proceeding would post a journal entry with no record to explain it. The
    refusal is a ``KeyError`` naming the id, and each of the three mutators with
    a lookup has its own.
    """
    led, records = coherent()
    op = S.OperationalBook(led, **records)  # type: ignore[arg-type]

    with pytest.raises(KeyError, match="unknown receivable invoice NOPE"):
        op.collect_receivable("NOPE", D("1.00"), 1, TS, TS)
    with pytest.raises(KeyError, match="unknown payable invoice NOPE"):
        op.pay_payable("NOPE", D("1.00"), 1, TS, TS)
    with pytest.raises(KeyError, match="unknown debt tranche NOPE"):
        op.repay_debt("NOPE", D("1.00"), 1, TS, TS)


# ==================================================== shared record helpers
def test_unique_ids_rejects_duplicate_and_mismatched_keys() -> None:
    """Kills the ``zip(strict=...)`` and key-mismatch mutants.

    ``_unique_ids`` is the guard behind all five record collections: a duplicate
    id, a mapping key that disagrees with the record it holds, and a mapping
    whose keys and values are different lengths all have to be refused. Dropping
    ``strict=True`` turns the length mismatch into a silent truncation that
    stops checking the tail of the mapping.
    """
    with pytest.raises(ValueError, match="record ids must be unique"):
        S._unique_ids({"AR-1": invoice("AR-1"), "AR-2": invoice("AR-1")}, "id")
    raises_value(
        "record key 'AR-OTHER' does not match record id 'AR-1'",
        lambda: S._unique_ids({"AR-OTHER": invoice("AR-1")}, "id"),
    )
    # A record whose id field is absent: getattr's default must stay None so
    # the zip comparison sees a non-string (record id None) instead of blowing
    # up with AttributeError inside the comprehension.
    raises_value(
        "record key 'AR-1' does not match record id None",
        lambda: S._unique_ids({"AR-1": invoice("AR-1")}, "no_such_field"),
    )


def test_record_ids_must_be_non_empty_strings() -> None:
    """Kills the ``isinstance``/emptiness ``or`` -> ``and`` mutant.

    A record filed under a non-string or empty id would make the subledger's
    mapping unreachable by the queries that look records up by id. Folding the
    two clauses into an ``and`` admits the non-string, which then raises
    ``TypeError`` from the mapping rather than the validator.
    """
    # A real record whose id field holds an int: the isinstance clause of the
    # guard has to fire, not the emptiness clause.
    raises_value(
        "issued_period must be a non-empty string",
        lambda: S._copy_records([invoice()], S.ReceivableInvoice, "issued_period"),
    )


def test_amounts_outside_the_quantum_are_refused_not_rounded() -> None:
    """Kills the ``cannot be represented at {quantum}`` mutants.

    Money is cent-quantised and rates carry twelve decimal places, and a value
    that cannot be represented exactly at that quantum is a refusal, not a
    silent rounding: a half-cent posting would make the subledger and the ledger
    disagree by construction. The same helper's parse failure has its own
    message, and both are rewritten by their mutants.
    """
    raises_value("amount cannot be represented at 0.01", lambda: S.money(D("1E+30")))
    raises_value("invalid decimal amount 'x'", lambda: S._to_decimal("x", S.CENT))


def test_a_coherent_book_still_reports_no_broken_rules() -> None:
    """The standing negative control, re-asserted after the batch above.

    Every rejection test in this module perturbs one thing at a time. This is
    the unperturbed state, and it must stay clean across all five rules.
    """
    led, records = coherent()

    op = S.OperationalBook(led, **records)  # type: ignore[arg-type]

    assert op.validate_reconciliations() == set()


def schedule(
    *, tranche_id: str = "D-1", period: int = 1, accrued: str = "100.00", paid: str = "0.00"
) -> S.AccruedInterestSchedule:
    return S.AccruedInterestSchedule(
        tranche_id,
        period,
        D(_PRINCIPAL),
        D(accrued),
        D(paid),
        D(_PRINCIPAL),
    )


def test_an_interest_schedule_key_must_name_its_own_record() -> None:
    """Kills the ``interest schedule key does not match its record`` mutants.

    The book indexes interest schedules by ``(tranche_id, period)`` and reads
    them by key when it validates a tranche. A key that disagrees with the record
    it points at would file one tranche's accrual under another's identity, and
    the interest reconciliation would then tie out against the wrong principal.
    The bound is exact, so the matching key is also asserted to be accepted.
    """
    copy = S._copy_interest_schedules
    good = schedule()

    assert copy({("D-1", 1): good}) == {("D-1", 1): good}
    raises_value(
        "interest schedule key does not match its record",
        lambda: copy({("D-1", 2): good}),
    )


def test_two_interest_schedules_for_the_same_tranche_and_period_are_rejected() -> None:
    """Kills the ``duplicate interest schedule`` mutants.

    A tranche accrues interest once per period, so two schedules for the same
    ``(tranche_id, period)`` would accrue it twice and one of them would
    overwrite the other in the copied mapping. The identity is the pair, not the
    tranche alone, which is why a second schedule for an adjacent period is
    accepted below.
    """
    copy = S._copy_interest_schedules
    first = schedule(period=1)
    second = schedule(period=1, accrued="200.00")

    raises_value(
        f"duplicate interest schedule {('D-1', 1)}",
        lambda: copy([first, second]),
    )
    assert copy([schedule(period=1), schedule(period=2)]) == {
        ("D-1", 1): schedule(period=1),
        ("D-1", 2): schedule(period=2),
    }


def test_interest_schedules_reach_the_book_as_records_or_keyed_pairs() -> None:
    """Kills the mapping/iterable branch mutants of the copy helper.

    The book accepts interest schedules either keyed or bare, and both spellings
    have to reach the same guards -- a non-record is what the type guard is for,
    and a key naming a different period is what the key guard is for. If one
    branch skipped a guard, that entry point would silently accept records the
    other rejects.
    """
    copy = S._copy_interest_schedules
    good = schedule()

    for bad in ([("D-1", 1)], {("D-1", 1): "not-a-record"}, [(("D-1", 1), good)]):
        with pytest.raises(TypeError, match="expected AccruedInterestSchedule detail record"):
            copy(bad)
    raises_value(
        "interest schedule key does not match its record",
        lambda: copy({("D-OTHER", 1): good}),
    )
    assert ZERO == D("0.00")


# ======================================================== entry-id sequencing
def _decoy(entry_id: str) -> L.JournalEntry:
    """A well-formed, balance-neutral entry whose only role is its id.

    Every reconciliation rule in the book compares records against ledger
    balances, so a decoy that moved an account would be rejected as a genuine
    mismatch before the sequence scan ever ran. Posting it between two
    accounts the subledger does not reconcile leaves the balances alone, so the
    only thing under test is which ids the scan counts.
    """
    return L.make(entry_id, ENTITY, 1, "sale_on_credit", {"sga": D("1.00")}, {"revenue": D("1.00")}, TS, TS)


def test_reopening_resumes_the_entry_sequence_rather_than_restarting_it() -> None:
    """Kills the ``_last_entry_sequence`` scan mutants in ``OperationalBook``.

    The book derives its next entry number from the ids already in the ledger, so
    a ledger that already holds ``m41-00000007-...`` must be continued at 8. The
    scan is a filter over id *shape*, so each of its conditions is load-bearing:
    the ``m41`` prefix, the digit test on the middle field, the field count, and
    which field holds the sequence. A book that scanned too widely would count a
    foreign id and skip numbers; one that scanned too narrowly would restart at
    1 and collide with a posting already in the ledger.
    """
    led, records = coherent()
    led.post(_decoy("m41-00000007-sale_on_credit"))

    op = S.OperationalBook(led, **records)  # type: ignore[arg-type]

    assert op._last_entry_sequence() == 7
    op.issue_receivable("AR-2", ENTITY, 1, 2, D(_AR), TS, TS)
    ids = [entry.entry_id for entry in op.ledger.entries]
    assert "m41-00000008-sale_on_credit" in ids


def test_the_entry_sequence_ignores_entries_it_does_not_own() -> None:
    """Kills the prefix, field-count, field-index and digit-test mutants.

    Three kinds of entry sit in the same ledger and none of them may be counted:
    a foreign-prefixed entry belonging to a different subsystem, a same-prefix
    entry whose middle field is not a number, and a same-prefix entry whose
    sequence sits in a different field. Counting any of them would move the next
    sequence number, so this asserts both directions -- that they are ignored,
    and that a real ``m41`` entry is still found alongside them.
    """
    led, records = coherent()
    led.post(_decoy("m42a-00000009-issue_common_shares"))
    led.post(_decoy("m41-abcdefg-sale_on_credit"))
    led.post(_decoy("m41-sale_on_credit-00000006"))
    led.post(_decoy("m41-00000004-sale_on-credit"))

    op = S.OperationalBook(led, **records)  # type: ignore[arg-type]

    assert op._last_entry_sequence() == 4


def test_a_hyphenated_event_name_still_yields_its_entry_sequence() -> None:
    """Kills the ``split``/``rsplit``/maxsplit mutants in the sequence scan.

    The scan splits each id into exactly three fields, so the *event* part may
    itself contain hyphens and must not be split further: ``m41-00000007-x-y``
    is one entry numbered 7, not two candidates. That is what fixes the split as
    ``split("-", 2)`` rather than ``rsplit`` or a smaller maxsplit -- a book
    reopening from a ledger whose entry ids it did not author has to read the
    sequence out of the second field regardless of how many hyphens follow.
    """
    led, records = coherent()
    led.post(_decoy("m41-00000007-sale-on-credit"))

    op = S.OperationalBook(led, **records)  # type: ignore[arg-type]

    assert op._last_entry_sequence() == 7


def test_a_ledger_with_no_operational_entries_starts_the_sequence_at_zero() -> None:
    """Kills the ``max(sequences, default=...)`` mutants.

    With nothing of its own to scan, the book has to report 0 so that the first
    entry it posts is numbered 1. A default of 1 would skip a number for no
    reason, which is how an audit trail acquires a gap.
    """
    led = book([opening()])

    op = S.OperationalBook(led)

    assert op._last_entry_sequence() == 0
    op.issue_receivable("AR-1", ENTITY, 1, 2, D(_AR), TS, TS)
    assert [e.entry_id for e in op.ledger.entries if e.entry_id.startswith("m41-")] == [
        "m41-00000001-sale_on_credit"
    ]


def test_a_record_belonging_to_another_entity_is_rejected() -> None:
    """Kills the ``entity_id != ledger.entity_id`` mutants in all five blocks.

    This is the one per-record check in ``validate_reconciliations`` that
    construction genuinely cannot enforce: an invoice is perfectly valid on its
    own, and only the book knows which entity it belongs to. Folding it into the
    surrounding ``or`` chain is what the mutants do, and it lets another
    company's receivable sit in this company's AR.
    """
    led, _ = coherent()

    raises_value(
        rule(S.AR_RULE),
        lambda: S.OperationalBook(led, **perturbed(receivables=[invoice(entity_id=OTHER)])),
    )


# ================================================= zero-amount mutator guards
def _simple_book() -> S.OperationalBook:
    """A valid book whose ledger carries only the cash opening entry."""
    led = book([opening()])
    return S.OperationalBook(led)  # type: ignore[arg-type]


def _populated_book() -> tuple[S.OperationalBook, str, str, str]:
    """A valid book holding one receivable, one payable, and one tranche."""
    led = book([opening(cash="10000.00", credit="10000.00")])
    op = S.OperationalBook(led)  # type: ignore[arg-type]
    op.issue_receivable("AR-1", ENTITY, 1, 2, "500.00", TS, TS)
    op.issue_inventory_payable("AP-1", "L-1", ENTITY, 1, 2, "400.00", TS, TS)
    op.borrow_debt("T-1", ENTITY, 1, 4, "1000.00", "0.04", TS, TS)
    return op, "AR-1", "AP-1", "T-1"


def test_mutators_reject_a_zero_amount() -> None:
    """Kills the ``allow_zero`` mutants of every amount-taking mutator.

    Each mutator funnels its amount through ``money(..., allow_zero=False)``.
    Dropping the keyword (falling back to ``allow_zero=True``) or passing
    ``True`` explicitly lets a zero posting through, which would mint an
    empty journal entry and desynchronise the entry sequence.
    """
    raises_value(
        "amount must be positive",
        lambda: _simple_book().issue_receivable("AR-Z", ENTITY, 1, 2, "0.00", TS, TS),
    )
    raises_value(
        "amount must be positive",
        lambda: _simple_book().issue_inventory_payable("AP-Z", "L-Z", ENTITY, 1, 2, "0.00", TS, TS),
    )
    raises_value(
        "amount must be positive",
        lambda: _simple_book().acquire_ppe_asset("PPE-Z", ENTITY, 1, "0.00", "0.00", 5, TS, TS),
    )
    raises_value(
        "amount must be positive",
        lambda: _simple_book().borrow_debt("T-Z", ENTITY, 1, 5, "0.00", "0.04", TS, TS),
    )
    raises_value("amount must be positive", lambda: _simple_book().consume_inventory("0.00", 1, TS, TS))
    raises_value("amount must be positive", lambda: _simple_book().pay_accrued_interest("0.00", 1, TS, TS))


def test_settlement_mutators_reject_a_zero_amount() -> None:
    """Kills the remaining ``allow_zero`` mutants on the settlement mutators.

    These run after a record lookup, so each needs its record to exist; the
    guard itself is the same ``money(..., allow_zero=False)`` call.
    """
    op, ar, ap, tr = _populated_book()
    raises_value("amount must be positive", lambda: op.collect_receivable(ar, "0.00", 2, TS, TS))
    raises_value("amount must be positive", lambda: op.pay_payable(ap, "0.00", 2, TS, TS))
    raises_value("amount must be positive", lambda: op.repay_debt(tr, "0.00", 2, TS, TS))


# ==================================================== pay_accrued_interest
def _accrued_book() -> tuple[S.OperationalBook, str, str]:
    """A valid book with two tranches, each carrying one accrued quarter.

    Interest accrues strictly after the issue period, so tranches issued in
    period 1 accrue their first quarter in period 2.
    """
    led = book([opening(cash="10000.00", credit="10000.00")])
    op = S.OperationalBook(led)  # type: ignore[arg-type]
    op.borrow_debt("T-1", ENTITY, 1, 8, "1000.00", "0.04", TS, TS)
    op.borrow_debt("T-2", ENTITY, 1, 8, "1000.00", "0.04", TS, TS)
    op.accrue_interest("T-1", 2, TS, TS)
    op.accrue_interest("T-2", 2, TS, TS)
    return op, "T-1", "T-2"


def test_a_payment_targets_only_the_requested_tranche_within_period() -> None:
    """Kills ``pay_accrued_interest`` mutants 18, 19 and 22.

    Mutant 22 flips the tranche match to ``!=`` so a named tranche's own
    schedules are excluded and the payment is refused as over-available.
    Mutants 18/19 break the conjunction, letting schedules of another tranche
    or a future period satisfy the availability check.
    """
    op, t1, _ = _accrued_book()
    # mutant 22: the requested tranche's own accrued interest is invisible, so
    # its exact unpaid amount would be rejected as over-available.
    paid = op.pay_accrued_interest("10.00", 2, TS, TS, tranche_id=t1)
    assert paid == D("10.00")
    # mutants 18/19: the filter widens beyond (this tranche, this period), so
    # T-2's schedule would fund a payment larger than T-1's own accrual.
    fresh, t1, _ = _accrued_book()
    raises_value(
        "payment exceeds accrued unpaid interest",
        lambda: fresh.pay_accrued_interest("20.00", 2, TS, TS, tranche_id=t1),
    )


def test_a_payment_cannot_reach_a_future_period_schedule() -> None:
    """Kills ``pay_accrued_interest`` mutant 19 directly.

    Period 2 schedules exist but the payment names period 1; widening the
    period test into a disjunction would let them fund the payment.
    """
    led = book([opening(cash="10000.00", credit="10000.00")])
    op = S.OperationalBook(led)  # type: ignore[arg-type]
    op.borrow_debt("T-1", ENTITY, 1, 8, "1000.00", "0.04", TS, TS)
    op.accrue_interest("T-1", 2, TS, TS)
    raises_value(
        "payment exceeds accrued unpaid interest",
        lambda: op.pay_accrued_interest("10.00", 1, TS, TS, tranche_id="T-1"),
    )


def test_a_valid_book_with_accrued_interest_reconciles() -> None:
    """Kills ``validate_reconciliations`` mutant 276.

    The contiguity clause ``prior is not None and period > issued_period + 1``
    turned into a disjunction makes the period-1 schedule take the
    ``prior.ending_principal`` branch with ``prior`` still ``None``; the
    resulting ``AttributeError`` must not be swallowed into a broken rule.
    """
    led = book([opening(cash="10000.00", credit="10000.00")])
    op = S.OperationalBook(led)  # type: ignore[arg-type]
    op.borrow_debt("T-1", ENTITY, 1, 8, "1000.00", "0.04", TS, TS)
    op.accrue_interest("T-1", 2, TS, TS)

    assert op.validate_reconciliations() == set()


# ==================================================== debt schedule chain
def test_an_interest_schedule_may_not_sit_at_the_issue_period() -> None:
    """Kills ``validate_reconciliations`` mutant 247.

    The schedule-period guard rejects a schedule dated at the tranche's issue
    period; narrowing ``<=`` to ``<`` lets it through, and with it a quarter of
    interest the debt was never outstanding for.
    """
    led, records = coherent()
    schedule = S.AccruedInterestSchedule("T-1", 1, D("10000.00"), D("100.00"), ZERO, D("10000.00"))
    raises_value(
        "invalid operational book: ['ACCT-DEBT-SCHEDULE']",
        lambda: S.OperationalBook(  # type: ignore[arg-type]
            led, **{**records, "accrued_interest": [schedule]}
        ),
    )


def test_a_chain_that_restarts_mid_stream_is_rejected() -> None:
    """Rejects a two-quarter schedule chain on a book with no accrual postings.

    Positive control for the debt-schedule validator: schedules present in the
    book must tie to the ledger's interest-payable control, so an injected
    chain with no matching accrual entries is rejected. Mutants 269/275/278's
    differing states all require a pre-issue schedule, which the schedule-period
    guard rejects first (see mutation_triage_overrides.json), so no killing
    test can reach them -- they are proven equivalent instead.
    """
    led, records = coherent()
    schedules = [
        S.AccruedInterestSchedule("T-1", 1, D("10000.00"), D("100.00"), ZERO, D("10000.00")),
        S.AccruedInterestSchedule("T-1", 2, D("10000.00"), D("100.00"), ZERO, D("10000.00")),
    ]
    raises_value(
        "invalid operational book: ['ACCT-DEBT-SCHEDULE']",
        lambda: S.OperationalBook(  # type: ignore[arg-type]
            led, **{**records, "accrued_interest": schedules}
        ),
    )


def test_a_multi_quarter_interest_chain_reconciles() -> None:
    """A valid two-quarter interest chain reconciles cleanly.

    Positive control for the contiguity ternary's ``prior.ending_principal``
    branch: quarter two must chain from quarter one's ending principal and the
    whole book must stay clean. Mutants 275/278 differ from the original only
    where a pre-issue schedule exists (rejected earlier by the schedule-period
    guard), so they are proven equivalent in mutation_triage_overrides.json
    rather than killed here.
    """
    led = book([opening(cash="10000.00", credit="10000.00")])
    op = S.OperationalBook(led)  # type: ignore[arg-type]
    op.borrow_debt("T-1", ENTITY, 1, 8, "1000.00", "0.04", TS, TS)
    op.accrue_interest("T-1", 2, TS, TS)
    op.accrue_interest("T-1", 3, TS, TS)

    assert op.validate_reconciliations() == set()


# ============================================ allow_zero on opening mutators
def test_opening_mutators_reject_a_zero_amount() -> None:
    """Kills the ``allow_zero`` mutants of the opening mutators.

    The earlier zero-amount test covers mutators whose ``_require_valid``
    runs before the money coercion; these five build records first, so the
    mutated ``money(...)`` call (dropped keyword or ``allow_zero=True``) never
    sees a zero amount in that ordering. Driving the zero directly at each
    opening mutator is the honest probe.
    """
    led = book([opening(cash="10000.00", credit="10000.00")])
    op = S.OperationalBook(led)  # type: ignore[arg-type]
    raises_value(
        "amount must be positive", lambda: op.acquire_ppe_asset("P-Z", ENTITY, 1, "0.00", "0.00", 5, TS, TS)
    )
    raises_value(
        "amount must be positive", lambda: op.borrow_debt("T-Z", ENTITY, 1, 5, "0.00", "0.04", TS, TS)
    )
    raises_value(
        "amount must be positive",
        lambda: op.issue_inventory_payable("AP-Z", "L-Z", ENTITY, 1, 2, "0.00", TS, TS),
    )
    raises_value("amount must be positive", lambda: op.issue_receivable("AR-Z", ENTITY, 1, 2, "0.00", TS, TS))
    raises_value(
        "amount must be positive", lambda: op.pay_accrued_interest("0.00", 2, TS, TS, tranche_id=None)
    )


def test_a_payment_only_consumes_the_named_tranches_schedules() -> None:
    """Kills ``pay_accrued_interest`` mutant 22 (``==`` flipped to ``!=``).

    The tranche filter must *include* the named tranche's own schedules. Under
    the flip the plan's paid schedule is the other tranche's; the cash posting
    and the interest-payable movements then disagree about which tranche's
    schedule was paid, which the debt reconciliation's per-tranche tie-out
    exposes.
    """
    op, t1, t2 = _accrued_book()
    op.pay_accrued_interest("10.00", 2, TS, TS, tranche_id=t1)
    schedules = {(k[0], k[1]): v for k, v in op.accrued_interest.items()}
    t1_paid = schedules[(t1, 2)].paid_amount
    t2_paid = schedules[(t2, 2)].paid_amount
    assert t1_paid == D("10.00")
    assert t2_paid == ZERO
