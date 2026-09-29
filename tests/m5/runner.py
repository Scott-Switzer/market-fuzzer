"""Execute M5 event sequences on both sides and compare after every event.

The runner applies each business event to the vendored FWF kernel and to the
independent ``python-accounting`` oracle, then compares the normalized,
cent-exact trial balance after **every** successful business event, so a
divergence is localized to the first operation that disagrees. Period close is
an event like any other and is compared as well.

On a mismatch the first divergence is reported with its seed, sequence index,
event index, the event, both normalized trial balances, and the per-account
deltas. A failing sequence is never discarded or regenerated: the seed is
reported so it can be replayed directly.
"""

from __future__ import annotations

from collections.abc import Sequence as SequenceABC
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from tests.m5.chart import FWF_ACCOUNTS
from tests.m5.events import EventType
from tests.m5.fwf_side import FwfCompany
from tests.m5.oracle_db import oracle_company
from tests.m5.sequences import GeneratedSequence

ZERO = Decimal("0.00")


@dataclass
class Mismatch:
    """The first divergence between the FWF kernel and the oracle."""

    seed: int
    sequence_index: int
    event_index: int
    event_type: str
    event: dict[str, Any]
    fwf_balance: dict[str, str]
    oracle_balance: dict[str, str]
    account_deltas: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "sequence_index": self.sequence_index,
            "event_index": self.event_index,
            "event_type": self.event_type,
            "event": self.event,
            "fwf_normalized_trial_balance": self.fwf_balance,
            "oracle_normalized_trial_balance": self.oracle_balance,
            "per_account_deltas": self.account_deltas,
        }


@dataclass
class SequenceResult:
    seed: int
    sequence_index: int
    events: int
    checkpoints: int
    matched_checkpoints: int
    mismatch: Mismatch | None = None

    @property
    def passed(self) -> bool:
        return self.mismatch is None


def _event_payload(event) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "entity_id": event.entity_id,
        "period": event.period,
        "event_type": event.event_type.value,
        "amount": event.amount,
        "reference": event.reference,
        "due_period": event.due_period,
        "quantity": event.quantity,
        "rate": event.rate,
        "useful_life": event.useful_life,
        "effective_date": event.effective_date,
    }


def _deltas(fwf: dict[str, Decimal], oracle: dict[str, Decimal]) -> dict[str, Decimal]:
    return {name: fwf[name] - oracle[name] for name in FWF_ACCOUNTS}


def run_sequence(sequence: GeneratedSequence) -> SequenceResult:
    """Run one sequence through both sides, comparing after every event."""
    company = FwfCompany(entity_id=sequence.events[0].entity_id)
    result = SequenceResult(
        seed=sequence.seed,
        sequence_index=sequence.index,
        events=len(sequence.events),
        checkpoints=0,
        matched_checkpoints=0,
    )
    with oracle_company(sequence.events[0].entity_id) as oracle:
        for index, event in enumerate(sequence.events):
            company.apply(event)
            oracle.apply(event)
            fwf = company.trial_balance()
            theirs = oracle.trial_balance()
            result.checkpoints += 1
            deltas = _deltas(fwf, theirs)
            offenders = {name: value for name, value in deltas.items() if value != ZERO}
            if offenders:
                result.mismatch = Mismatch(
                    seed=sequence.seed,
                    sequence_index=sequence.index,
                    event_index=index,
                    event_type=event.event_type.value,
                    event=_event_payload(event),
                    fwf_balance={name: str(fwf[name]) for name in FWF_ACCOUNTS},
                    oracle_balance={name: str(theirs[name]) for name in FWF_ACCOUNTS},
                    account_deltas={name: str(value) for name, value in deltas.items()},
                )
                return result
            result.matched_checkpoints += 1
    return result


def run_sequences(
    sequences: SequenceABC[GeneratedSequence],
) -> tuple[list[SequenceResult], list[dict[str, int]]]:
    """Run every sequence and return per-sequence results plus coverage counts."""
    from tests.m5.sequences import coverage

    results = [run_sequence(sequence) for sequence in sequences]
    return results, coverage(list(sequences))


def unsupported_operations() -> list[dict[str, str]]:
    """Return the operations the oracle cannot faithfully represent.

    The M5 oracle implements every operation in
    :data:`tests.m5.events.EventType`, so this list is empty. It exists so the
    evidence artifact states the classification explicitly rather than by
    omission.
    """
    return [
        {
            "event_type": event.value,
            "classification": "SUPPORTED",
            "reason": "independently mapped into python-accounting",
        }
        for event in EventType
    ]
