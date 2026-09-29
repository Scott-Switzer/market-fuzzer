"""Planted accounting defects: proof that the M5 oracle can disagree with FWF.

A differential oracle is only worth running if it is capable of *failing*. This
module is the anti-tautology apparatus for M5.1: each entry below deliberately
corrupts the FWF side of one business event, and the unmodified
``python-accounting`` oracle must notice.

The defects are validation experiments. Nothing here is production code and
nothing here is a fix: the corrupt postings exist only inside
:class:`PlantedCompany`, which subclasses the FWF-side company used by the
clean oracle run. The clean :class:`~tests.m5.fwf_side.FwfCompany` and the
vendored kernel are never modified.

Each defect states the accounts it is expected to perturb, so a test can assert
that the oracle disagreed for the *right* reason and not by accident.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app._vendor.fwf_kernel import ledger as L
from tests.m5.chart import FWF_ACCOUNTS
from tests.m5.events import BusinessEvent, EventType
from tests.m5.fwf_side import FwfCompany, close_instant, period_instant
from tests.m5.oracle_db import oracle_company
from tests.m5.sequences import GeneratedSequence

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class PlantedDefect:
    """One deliberately wrong FWF posting, and the accounts it must disturb."""

    defect_id: str
    title: str
    event_type: EventType
    description: str
    expected_accounts: frozenset[str]
    inject: Callable[[PlantedCompany, BusinessEvent, Decimal], None]

    def as_dict(self) -> dict[str, Any]:
        return {
            "defect_id": self.defect_id,
            "title": self.title,
            "event_type": self.event_type.value,
            "description": self.description,
            "expected_accounts": sorted(self.expected_accounts),
        }


class PlantedCompany(FwfCompany):
    """An FWF-side company that injects exactly one accounting defect."""

    def __init__(self, entity_id: str, defect: PlantedDefect) -> None:
        super().__init__(entity_id=entity_id)
        self.defect = defect
        self.injections = 0

    def apply(self, event: BusinessEvent) -> None:
        """Apply the event normally, then corrupt the one targeted posting."""
        super().apply(event)
        if event.event_type is not self.defect.event_type:
            return
        self._slot = max(self._slot, 1)
        amount = Decimal(event.amount)
        self.defect.inject(self, event, amount)
        self.injections += 1


# --------------------------------------------------------------------- defects
def _reverse_sga_debit_credit(company: PlantedCompany, event: BusinessEvent, amount: Decimal) -> None:
    """Mirror the SG&A posting so the expense is booked as income.

    The kernel posts ``debit sga / credit cash``. The mirror makes the net
    effect ``credit sga / debit cash``: the debit/credit mapping of a real
    operating expense is reversed.
    """
    stamp = period_instant(event.period, min(company._slot + 1, 43))
    company.ledger.post(
        L.make(
            f"m5-planted-rev-sga-{event.event_id}",
            company.entity_id,
            event.period,
            "pay_sga",
            {"cash": amount},
            {"sga": amount},
            stamp,
            stamp,
        )
    )


def _drop_last_entry(company: PlantedCompany, event_name: str) -> bool:
    """Remove the most recent ledger entry for ``event_name``.

    The defect is a *missing* posting rather than an unbalanced one, so it is
    applied by withdrawing the entry the kernel just made rather than by
    inventing a new one.
    """
    ledger = company.ledger
    for position in range(len(ledger.entries) - 1, -1, -1):
        if ledger.entries[position].event == event_name:
            del ledger.entries[position]
            return True
    return False


def _omit_ap_purchase_posting(company: PlantedCompany, event: BusinessEvent, amount: Decimal) -> None:
    """Suppress the inventory purchase posting, payable leg included.

    The kernel posts ``debit inventory / credit accounts payable`` as a single
    balanced entry, so the faithful "the AP posting never happened" defect is to
    drop the entry: goods are received but neither the asset nor the payable is
    recorded.
    """
    _drop_last_entry(company, "buy_inventory_on_credit")


def _omit_accumulated_depreciation(company: PlantedCompany, event: BusinessEvent, amount: Decimal) -> None:
    """Remove the period's accumulated-depreciation recognition.

    The kernel posts ``debit depreciation / credit accumulated depreciation``
    from its own PP&E schedule. Removing that entry leaves the asset un-depreciated
    for the period, which is the classic omitted-accumulated-depreciation error.
    """
    ledger = company.ledger
    for position in range(len(ledger.entries) - 1, -1, -1):
        entry = ledger.entries[position]
        if entry.event == "depreciate" and entry.period == event.period:
            del ledger.entries[position]
            return


def _misclassify_interest_payable(company: PlantedCompany, event: BusinessEvent, amount: Decimal) -> None:
    """Reclassify accrued interest from interest payable into trade payables.

    The kernel credits ``interest_payable`` when it accrues interest. The
    defect moves the whole accrued balance to ``ap``, which is a real and
    extremely common classification error. The amount moved is the defective
    ledger's own balance -- the defect corrupts the system under test and never
    consults the oracle.
    """
    due = company.ledger.balances()["interest_payable"]
    if due <= ZERO:
        return
    stamp = period_instant(event.period, min(company._slot + 1, 43))
    company.ledger.post(
        L.make(
            f"m5-planted-reclass-interest-{event.event_id}",
            company.entity_id,
            event.period,
            "accrue_interest",
            {"interest_payable": due},
            {"ap": due},
            stamp,
            stamp,
        )
    )


def _alter_common_stock_apic(company: PlantedCompany, event: BusinessEvent, amount: Decimal) -> None:
    """Shift the share premium out of APIC and into common stock.

    Issuing shares above par credits ``additional_paid_in_capital``. The defect
    books part of that premium as ``common_stock`` instead, so the par value of
    common stock no longer equals par times shares issued.
    """
    apic = company.ledger.balances()["additional_paid_in_capital"]
    if apic <= ZERO:
        # Nothing was issued above par; overstate paid-in capital instead so
        # the misallocation is still non-degenerate.
        apic = company.equity.share_classes["common"].par_value * 10
    stamp = period_instant(event.period, min(company._slot + 1, 43))
    company.ledger.post(
        L.make(
            f"m5-planted-apic-{event.event_id}",
            company.entity_id,
            event.period,
            "issue_common_shares",
            {"additional_paid_in_capital": apic},
            {"common_stock": apic},
            stamp,
            stamp,
        )
    )


def _skip_retained_earnings_close(company: PlantedCompany, event: BusinessEvent, amount: Decimal) -> None:
    """Unwind the period close so retained earnings never receives the result.

    ``Ledger.close`` moves revenue, expense and dividends into retained
    earnings. The defect lets the close happen and then posts its exact mirror
    into the next open period, which is the observable trace of a close transfer
    that was silently skipped. The mirror is derived from the defective ledger's
    own close entry, never from the oracle.
    """
    ledger = company.ledger
    close_id = f"close-{event.period}"
    close = next((e for e in ledger.entries if e.entry_id == close_id), None)
    if close is None:
        return
    debits = {ln.account: ln.credit for ln in close.lines if ln.credit > ZERO}
    credits = {ln.account: ln.debit for ln in close.lines if ln.debit > ZERO}
    if not debits or not credits:
        return
    stamp = close_instant(event.period + 1)
    ledger.post(
        L.make(
            f"m5-planted-undo-close-{event.period}",
            company.entity_id,
            event.period + 1,
            "close",
            debits,
            credits,
            stamp,
            stamp,
        )
    )


DEFECTS: tuple[PlantedDefect, ...] = (
    PlantedDefect(
        defect_id="reversed_debit_credit",
        title="Reversed debit/credit mapping on an operating expense",
        event_type=EventType.SGA,
        description=(
            "The SG&A posting is mirrored, so the expense is credited and cash debited "
            "instead of the other way round."
        ),
        expected_accounts=frozenset({"sga", "cash"}),
        inject=_reverse_sga_debit_credit,
    ),
    PlantedDefect(
        defect_id="omitted_ap_posting",
        title="Omitted accounts-payable purchase posting",
        event_type=EventType.INVENTORY_PURCHASE,
        description=(
            "The inventory purchase entry (debit inventory / credit accounts payable) "
            "is dropped, so goods are received with neither asset nor payable recorded."
        ),
        expected_accounts=frozenset({"inventory", "ap"}),
        inject=_omit_ap_purchase_posting,
    ),
    PlantedDefect(
        defect_id="omitted_accumulated_depreciation",
        title="Omitted accumulated depreciation",
        event_type=EventType.DEPRECIATION,
        description=(
            "The period's depreciation entry (debit depreciation / credit accumulated "
            "depreciation) is dropped, leaving the asset un-depreciated."
        ),
        expected_accounts=frozenset({"depreciation", "acc_dep"}),
        inject=_omit_accumulated_depreciation,
    ),
    PlantedDefect(
        defect_id="misclassified_interest_payable",
        title="Accrued interest misclassified as trade payables",
        event_type=EventType.INTEREST_ACCRUAL,
        description=(
            "The accrued interest balance is moved from interest payable into "
            "accounts payable, misstating both liabilities."
        ),
        expected_accounts=frozenset({"interest_payable", "ap"}),
        inject=_misclassify_interest_payable,
    ),
    PlantedDefect(
        defect_id="altered_common_stock_apic",
        title="Common stock / additional paid-in capital misallocation",
        event_type=EventType.SHARE_ISSUANCE,
        description=(
            "Share premium that belongs in additional paid-in capital is booked as "
            "common stock, so par capital no longer equals par times shares issued."
        ),
        expected_accounts=frozenset({"common_stock", "additional_paid_in_capital"}),
        inject=_alter_common_stock_apic,
    ),
    PlantedDefect(
        defect_id="skipped_retained_earnings_close",
        title="Skipped retained-earnings close transfer",
        event_type=EventType.PERIOD_CLOSE,
        description=(
            "The period close entry is mirrored into the next open period, so the "
            "period result never reaches retained earnings."
        ),
        expected_accounts=frozenset({"retained_earnings"}),
        inject=_skip_retained_earnings_close,
    ),
)

DEFECTS_BY_ID: dict[str, PlantedDefect] = {d.defect_id: d for d in DEFECTS}


# ------------------------------------------------------------------ detection
@dataclass
class PlantedResult:
    """The outcome of running one sequence with one defect planted."""

    defect_id: str
    sequence_seed: int
    injected: bool
    detected: bool
    event_index: int | None
    event_type: str | None
    offending_accounts: dict[str, str]
    perturbed_accounts: frozenset[str]
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "defect_id": self.defect_id,
            "sequence_seed": self.sequence_seed,
            "injection_applied": self.injected,
            "detected_by_oracle": self.detected,
            "event_index": self.event_index,
            "event_type": self.event_type,
            "offending_account_deltas": self.offending_accounts,
            "perturbed_accounts": sorted(self.perturbed_accounts),
            "detail": self.detail,
        }


def run_planted(defect: PlantedDefect, sequence: GeneratedSequence) -> PlantedResult:
    """Run ``sequence`` with ``defect`` planted and report whether it was caught.

    The oracle side is the unmodified, independent ``python-accounting``
    implementation. Detection means the very first checkpoint whose normalized
    deltas are non-zero, localized exactly as an unplanted divergence would be.
    """
    company = PlantedCompany(entity_id=sequence.events[0].entity_id, defect=defect)
    with oracle_company(sequence.events[0].entity_id) as oracle:
        for index, event in enumerate(sequence.events):
            company.apply(event)
            oracle.apply(event)
            fwf = company.trial_balance()
            theirs = oracle.trial_balance()
            deltas = {name: fwf[name] - theirs[name] for name in FWF_ACCOUNTS}
            offenders = {name: value for name, value in deltas.items() if value != ZERO}
            if offenders:
                return PlantedResult(
                    defect_id=defect.defect_id,
                    sequence_seed=sequence.seed,
                    injected=company.injections > 0,
                    detected=True,
                    event_index=index,
                    event_type=event.event_type.value,
                    offending_accounts={k: str(v) for k, v in offenders.items()},
                    perturbed_accounts=frozenset(offenders),
                    detail=(
                        f"oracle diverged at event {index} ({event.event_type.value}) on {sorted(offenders)}"
                    ),
                )
    return PlantedResult(
        defect_id=defect.defect_id,
        sequence_seed=sequence.seed,
        injected=company.injections > 0,
        detected=False,
        event_index=None,
        event_type=None,
        offending_accounts={},
        perturbed_accounts=frozenset(),
        detail="no divergence: the oracle did not notice the planted defect",
    )


def find_target_sequence(defect: PlantedDefect, sequences) -> GeneratedSequence:
    """Return the first sequence that actually exercises ``defect``'s event.

    A defect that targets an event the sequence never reaches would report
    "not detected" for the uninteresting reason that nothing happened. Choosing a
    sequence that contains the targeted event keeps the result meaningful.
    """
    for sequence in sequences:
        if any(e.event_type is defect.event_type for e in sequence.events):
            return sequence
    raise LookupError(f"no generated sequence contains a {defect.event_type.value} event")
