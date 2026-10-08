"""Non-intrusive session recording seam (M10.7).

A :class:`SessionRecorder` observes what a :class:`~app.benchmark.session.BenchmarkSession`
does through four narrow hook points:

1. *session start* -- subscribe to the exchange and bookkeeping state;
2. *decision boundary* -- the agent's exact pre-decision observation, the exact
   validated action it returned, and the L10 depth snapshot of the book state
   the observation was built from;
3. *command creation* -- every canonical exchange command (background and agent)
   at the moment it is created, before it is submitted;
4. *event deltas* -- new ``OrderEventV2`` entries appended to the immutable
   ledger since the previous delta.

The recorder is a pure observer. It must not mutate exchange state, orders,
fills, the RNG, or any digest; a benchmark run with and without a recorder must
produce byte-identical ``SessionResult`` values (proven by
``tests/test_corpus_recording.py``). Every hook is therefore ``-> None`` and
receives already-copied or immediately-consumable values.

The default is :class:`NullSessionRecorder`: no recorder, no behaviour change
-- the M10.6.1 default path does not even allocate a hook object.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from collections.abc import Mapping, Sequence

    from app.benchmark.universe import BenchmarkUniverse
    from app.exchange.v2_matching import MatchingExchangeV2


def canonical_json_bytes(value: Any) -> bytes:
    """Deterministic compact JSON for observation/action/payload documents."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _local_digest(value: Any) -> str:
    """SHA-256 over canonical JSON *without* importing app.benchmark.

    The recorder module sits at the boundary between the benchmark (which must
    import it) and the corpus package, so it deliberately avoids a module-level
    dependency on ``app.benchmark.hashing`` -- that cycle would make a plain
    ``import app.corpus`` spin around ``app.benchmark.__init__``.
    """

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@runtime_checkable
class SessionRecorder(Protocol):
    """The observation seam a corpus builder installs on a benchmark session."""

    def on_session_start(
        self,
        *,
        episode_id: str,
        universe: BenchmarkUniverse,
        exchange: MatchingExchangeV2,
        securities: Sequence[str],
    ) -> None: ...

    def on_decision(
        self,
        *,
        episode_id: str,
        decision_index: int,
        step: int,
        day_index: int,
        instrument: str,
        observation: Mapping[str, Any],
        action: Mapping[str, Any],
        book_snapshot: Mapping[str, Any],
    ) -> None: ...

    def on_command(self, *, episode_id: str, command: Any) -> None: ...

    def on_events(self, *, episode_id: str, events: Sequence[Any]) -> None: ...


@dataclass(slots=True)
class RecorderHooks:
    """Frozen counts of what a recording session observed.

    Used by the equivalence test to prove a recorded session saw exactly the
    same decision/command/event volume as a bare session.
    """

    decisions: int = 0
    commands: int = 0
    events: int = 0
    snapshots: int = 0
    #: Populated when a recorder-backed session ran: the recorder the session
    #: used, held only for the builder to finalize per-episode state.
    last_episode_id: str | None = None

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            "decisions": self.decisions,
            "commands": self.commands,
            "events": self.events,
            "snapshots": self.snapshots,
            "last_episode_id": self.last_episode_id,
        }


class NullSessionRecorder:
    """The default no-op recorder; ``None``-behaviour with a real object type."""

    def on_session_start(self, **_kwargs: Any) -> None:  # noqa: D105 - protocol no-op
        return

    def on_decision(self, **_kwargs: Any) -> None:
        return

    def on_command(self, **_kwargs: Any) -> None:
        return

    def on_events(self, **_kwargs: Any) -> None:
        return


@dataclass
class CountingRecorder:
    """A minimal concrete recorder for equivalence tests and diagnostics.

    It counts hook invocations and retains only bounded summary data (the last
    episode id and the max ledger watermark it saw); it never retains the whole
    event stream, so even this testing recorder stays memory-bounded.
    """

    hooks: RecorderHooks = field(default_factory=RecorderHooks)
    max_event_watermark: int = 0

    def on_session_start(
        self,
        *,
        episode_id: str,
        universe: BenchmarkUniverse,
        exchange: MatchingExchangeV2,
        securities: Sequence[str],
    ) -> None:
        self.hooks.last_episode_id = episode_id
        self.max_event_watermark = 0

    def on_decision(
        self,
        *,
        episode_id: str,
        decision_index: int,
        step: int,
        day_index: int,
        instrument: str,
        observation: Mapping[str, Any],
        action: Mapping[str, Any],
        book_snapshot: Mapping[str, Any],
    ) -> None:
        self.hooks.decisions += 1
        self.hooks.snapshots += 1

    def on_command(self, *, episode_id: str, command: Any) -> None:
        self.hooks.commands += 1

    def on_events(self, *, episode_id: str, events: Sequence[Any]) -> None:
        self.hooks.events += len(events)
        if events:
            self.max_event_watermark = max(self.max_event_watermark, len(events))
