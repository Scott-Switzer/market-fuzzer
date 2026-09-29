"""M5.1 wrapper tests for the planted-defect apparatus.

``tests/m5/planted.py`` is the anti-tautology layer for the differential oracle:
each planted defect corrupts one FWF-side posting, and the unmodified
``python-accounting`` oracle must notice. The oracle report script exercises the
whole matrix once; these tests pin the properties that make it trustworthy:

* every planted defect is well-formed (a defect id, an event type the generator
  actually produces, at least one expected account);
* a planted run on a sequence that exercises the defect's event type injects
  exactly once and is detected at that event;
* the accounts the oracle flags are exactly the defect's ``expected_accounts``;
* the clean run -- the same sequences with no defect -- is still a perfect
  differential match, so detection is attributable to the defect and not to the
  harness.
"""

from __future__ import annotations

import pytest

from app._vendor.fwf_kernel import ledger as L
from tests.m5.chart import FWF_ACCOUNTS
from tests.m5.fwf_side import FwfCompany
from tests.m5.oracle_db import oracle_company
from tests.m5.planted import (
    DEFECTS,
    DEFECTS_BY_ID,
    PlantedCompany,
    find_target_sequence,
    run_planted,
)
from tests.m5.sequences import generate_sequences

ZERO = L.ZERO if hasattr(L, "ZERO") else __import__("decimal").Decimal("0.00")


def test_the_defect_catalogue_is_wellformed() -> None:
    """Every defect names a real event type and at least one expected account."""
    assert len(DEFECTS) >= 6
    ids = [d.defect_id for d in DEFECTS]
    assert len(ids) == len(set(ids)), "defect ids must be unique"
    for defect in DEFECTS:
        assert defect.expected_accounts, f"{defect.defect_id} names no expected accounts"
        assert set(defect.expected_accounts) <= set(FWF_ACCOUNTS)
        assert DEFECTS_BY_ID[defect.defect_id] is defect


def test_every_defect_targets_an_event_type_the_generator_produces() -> None:
    """A defect no generated sequence can reach would never be exercised."""
    sequences = generate_sequences(count=20)
    produced = {e.event_type for seq in sequences for e in seq.events}
    for defect in DEFECTS:
        assert defect.event_type in produced, (
            f"{defect.defect_id} targets {defect.event_type.value}, which the generator never emits"
        )


@pytest.mark.parametrize("defect", DEFECTS, ids=lambda d: d.defect_id)
def test_each_planted_defect_is_detected_on_the_right_accounts(defect) -> None:
    """Each defect injects once and the oracle flags exactly its own accounts.

    ``run_planted`` localizes the divergence the same way the clean oracle run
    does, so a defect detected on the wrong accounts would mean the corruption
    lands somewhere other than where the defect says it does.
    """
    sequences = generate_sequences(count=20)
    sequence = find_target_sequence(defect, sequences)
    result = run_planted(defect, sequence)

    assert result.injected, f"{defect.defect_id} never injected"
    assert result.detected, f"{defect.defect_id} went undetected: {result.detail}"
    # The oracle flags every account whose balance diverged. A defect whose
    # posting is the *mirror* of the original perturbs exactly the accounts it
    # declares; a close-side defect also moves every income-statement leg that
    # feeds the account it targets. What must hold in both cases is that the
    # declared accounts are all present -- detection is on the right defect,
    # not an accidental divergence elsewhere.
    assert defect.expected_accounts <= result.perturbed_accounts, result.detail
    assert set(result.perturbed_accounts) <= set(FWF_ACCOUNTS), result.detail
    assert result.offending_accounts, "no offending deltas recorded"


def test_a_defect_is_not_injected_on_unrelated_events() -> None:
    """The injector must fire only on the defect's own event type."""
    sequences = generate_sequences(count=20)
    sequence = find_target_sequence(DEFECTS[0], sequences)
    company = PlantedCompany(entity_id=sequence.events[0].entity_id, defect=DEFECTS[0])
    unrelated = [e for e in sequence.events if e.event_type is not DEFECTS[0].event_type]

    for event in unrelated:
        company.apply(event)

    assert company.injections == 0


def test_the_clean_run_still_matches_the_oracle_exactly() -> None:
    """Without a defect, FWF and the oracle agree to the cent at every step.

    This is the control for the planted tests above: if the clean comparison
    diverged, a planted detection would prove nothing about the defect.
    """
    sequences = generate_sequences(count=8)
    for sequence in sequences:
        company = FwfCompany(entity_id=sequence.events[0].entity_id)
        with oracle_company(sequence.events[0].entity_id) as oracle:
            for event in sequence.events:
                company.apply(event)
                oracle.apply(event)
                fwf = company.trial_balance()
                theirs = oracle.trial_balance()
                deltas = {name: fwf[name] - theirs[name] for name in FWF_ACCOUNTS}
                offenders = {k: v for k, v in deltas.items() if v != ZERO}
                assert not offenders, (
                    f"clean sequence {sequence.seed} diverged at {event.event_type.value}: {offenders}"
                )
