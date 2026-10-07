"""The guarded corpus build path (M10.7).

:func:`build_corpus` is the trainer: it resolves a whole training plan, refuses
any plan that contains a non-``TRAINABLE`` world *before generating anything*,
materializes every planned world, runs its recorded session, streams the seven
``fwf-corpus-v1`` tables into bounded Parquet shards, and seals an immutable,
hash-committed release.

The build order is the security order:

1. resolve the whole plan (unknown family/ecology fail here);
2. assert every planned world is ``TRAINABLE`` -- the typed gate;
3. open the sibling ``.<name>.building`` temporary directory and writers;
4. only then generate worlds / construct reference policies.

Each episode runs a :class:`~app.benchmark.session.BenchmarkSession` with a
recorder sink installed; the sink converts hook calls into buffered rows and
drains event deltas incrementally, so the build never holds more than one
world's rows beyond the writer buffer. A successful release is promoted with an
atomic rename; a failed build leaves no release directory and no stray
temporary directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol

from app.benchmark.model import SessionResult, TaskKind, TaskOutcome
from app.benchmark.plan import (
    DatasetSplit,
    EvaluationPlan,
    PlannedWorld,
    default_ecology_registry,
    default_training_plan,
    plan_worlds,
    require_trainable,
)
from app.benchmark.port import accumulate_port, passive_maker_port, twap_port
from app.benchmark.process_registry import MarketProcessRegistry, default_process_registry
from app.benchmark.session import BenchmarkSession, SessionConfig, run_manifest
from app.benchmark.tasks import build_task_spec, evaluate
from app.benchmark.universe import BenchmarkUniverse, build_universe
from app.corpus.schema import AGENT_PROTOCOL_SCHEMA_VERSION, CORPUS_SCHEMA_VERSION, TABLE_SCHEMAS, table_names
from app.corpus.writer import TableWriter, WriterLimits
from app.market.calendar import trading_days

__all__ = [
    "CORPUS_POLICIES",
    "CORPUS_RELEASE_INFO_VERSION",
    "CorpusConfig",
    "CorpusManifest",
    "CorpusWriterStats",
    "build_corpus",
    "episode_id_for",
    "canonical_json_str",
]

CORPUS_RELEASE_INFO_VERSION = "corpus-manifest-v1"

#: The policy vocabulary the corpus CLI accepts (deterministic built-ins only).
CORPUS_POLICIES = ("twap", "maker", "accumulate")

_EPISODE_NAMESPACE = "fwf-corpus-recording-v1"


class _PortFactory(Protocol):
    """A factory constructing one decision port per planned world."""

    def __call__(self, index: int) -> Any: ...


@dataclass(frozen=True, slots=True)
class CorpusConfig:
    """Everything one corpus release is generated from.

    ``plan``/``registry``/``ecologies``/``port_factory`` are the trusted-call
    injection surface (programmatic callers may inject registries/plans); the
    participant-facing CLI passes only the published defaults
    (``m10_7_training_v1``, the public registry, the published ecologies, and a
    built-in policy port). Nothing exposes a private-family, registry-module,
    plan-path, or python-module knob.
    """

    output: Path
    task: TaskKind = TaskKind.EXECUTION
    policy: str = "twap"
    plan: EvaluationPlan | None = None
    registry: MarketProcessRegistry | None = None
    ecologies: Any | None = None
    port_factory: _PortFactory | None = None
    worlds: int = 32
    securities: int = 8
    days: int = 5
    steps_per_day: int = 30
    seed: int = 20_261_007
    book_depth: int = 10
    target_quantity: int = 50_000
    max_order_quantity: int = 20_000
    limits: WriterLimits | None = None
    #: Record git provenance when available from the build environment; absence
    #: of ``.git`` (an installed package) must never fail the build.
    record_git_provenance: bool = True

    def __post_init__(self) -> None:
        if self.book_depth < 1:
            raise ValueError("book_depth must be at least one level")
        if self.worlds < 1:
            raise ValueError("a corpus needs at least one world")
        if min(self.securities, self.days, self.steps_per_day) < 1:
            raise ValueError("securities, days, and steps_per_day must be positive")
        if self.policy not in CORPUS_POLICIES:
            raise ValueError(f"policy must be one of {CORPUS_POLICIES}")


def _git_commit() -> str | None:
    """Best-effort git provenance from the build environment; never fails."""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return ((result.stdout or "").strip()) or None


def _policy_port(policy: str, episode_index: int) -> Any:
    """Deterministic built-in policies for the v1 CLI scope."""

    if policy == "twap":
        return twap_port(slice_quantity=2_500, name=f"corpus-twap-{episode_index:04d}")
    if policy == "maker":
        return passive_maker_port(spread_ticks=3, quantity=200, name=f"corpus-maker-{episode_index:04d}")
    return accumulate_port(
        slice_quantity=1_000,
        max_shares_per_instrument=20_000,
        name=f"corpus-accumulate-{episode_index:04d}",
    )


def _agent_cash_cents(task: TaskKind) -> int:
    return {TaskKind.EXECUTION: 10**12, TaskKind.MARKET_MAKING: 10**13, TaskKind.PORTFOLIO: 10**9}[task]


def _calendar(days: int) -> tuple[date, ...]:
    start = date(2026, 6, 1)
    return tuple(trading_days(start, start + timedelta(days=2 * days + 7))[:days])


def episode_id_for(world: PlannedWorld, *, task: TaskKind) -> str:
    """The corpus-facing, deterministic episode identity for one planned world.

    Derived from the recorder namespace plus world identity and task, so the
    same generation settings reproduce the same episode ids. Distinct from
    ``world_id`` on purpose: an episode is a recorded *run* of the world.
    """

    # Exactly the derivation the recorded session applies (see
    # ``BenchmarkSession._episode_identifier``), so rows written from either
    # side carry the same episode id. The universe id is the runner's naming:
    # ``synth-exchange-<task>-<index:04d>``.
    payload = {
        "namespace": _EPISODE_NAMESPACE,
        "world_id": world.world_id,
        "universe_id": f"synth-exchange-{task.value}-{world.index:04d}",
        "seed": world.seed,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "ep-" + hashlib.sha256(encoded).hexdigest()[:32]


def canonical_json_str(value: Mapping[str, Any]) -> str:
    """The canonical JSON string stored in observation/action/payload fields."""

    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


# --- recorder sink --------------------------------------------------------------------


class _RecordingSink:
    """Converts recorder hook calls into buffered ``fwf-corpus-v1`` rows.

    One sink per episode. Event deltas go to the events writer immediately, so
    the sink never accumulates the whole episode's events; decision/snapshot/
    command rows are staged per world and handed over when the episode
    completes (they are small relative to the event stream).
    """

    def __init__(
        self,
        *,
        episode_id: str,
        writers: dict[str, TableWriter],
        snapshot_depth: int,
        policy_id: str,
    ) -> None:
        self.episode_id = episode_id
        self.writers = writers
        self.snapshot_depth = snapshot_depth
        self.policy_id = policy_id
        self.decision_rows: list[dict[str, Any]] = []
        self.snapshot_rows: list[dict[str, Any]] = []
        self.command_ordinal = 0
        self.commands_streamed = 0
        self.events_recorded = 0

    def on_session_start(self, **_kwargs: Any) -> None:
        """Universe/securities rows are emitted by the builder after the run."""

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
        snapshot_id = book_snapshot.get("snapshot_id")
        if snapshot_id:
            bid_levels = book_snapshot.get("bids") or []
            ask_levels = book_snapshot.get("asks") or []
            self.snapshot_rows.append(
                {
                    "corpus_schema_version": CORPUS_SCHEMA_VERSION,
                    "snapshot_id": str(snapshot_id),
                    "episode_id": episode_id,
                    "decision_index": decision_index,
                    "global_step": int(book_snapshot.get("global_step", step)),
                    "instrument_id": str(book_snapshot.get("instrument_id", instrument)),
                    "book_depth": int(book_snapshot.get("book_depth", self.snapshot_depth)),
                    "best_bid_ticks": book_snapshot.get("best_bid_ticks"),
                    "best_ask_ticks": book_snapshot.get("best_ask_ticks"),
                    "bids": [
                        {"price_ticks": int(price), "quantity": int(quantity)}
                        for price, quantity in bid_levels
                    ],
                    "asks": [
                        {"price_ticks": int(price), "quantity": int(quantity)}
                        for price, quantity in ask_levels
                    ],
                }
            )
        self.decision_rows.append(
            {
                "corpus_schema_version": CORPUS_SCHEMA_VERSION,
                "episode_id": episode_id,
                "decision_index": decision_index,
                "global_step": step,
                "day_index": day_index,
                "instrument_id": instrument,
                "observation_schema_version": str(
                    observation.get("schema_version", AGENT_PROTOCOL_SCHEMA_VERSION)
                ),
                "action_schema_version": str(action.get("schema_version", AGENT_PROTOCOL_SCHEMA_VERSION)),
                # Canonical JSON documents: exactly what the agent saw / returned.
                "observation_json": canonical_json_str(dict(observation)),
                "action_json": canonical_json_str(dict(action)),
                "action_type": str(action.get("action_type", "hold")),
                "side": action.get("side"),
                "order_type": action.get("order_type"),
                "quantity": action.get("quantity"),
                "limit_price_ticks": action.get("limit_price_ticks"),
                "order_id": action.get("order_id"),
                "decision_status": "executed",
                "ledger_event_index_before": int(book_snapshot.get("ledger_event_index_before", 0)),
                "ledger_event_index_after": int(book_snapshot.get("ledger_event_index_after", 0)),
                "book_snapshot_id": str(snapshot_id) if snapshot_id else None,
            }
        )

    def on_command(self, *, episode_id: str, command: Any) -> None:
        kind = type(command).__name__
        row: dict[str, Any] = {
            "corpus_schema_version": CORPUS_SCHEMA_VERSION,
            "episode_id": episode_id,
            "command_ordinal": self.command_ordinal,
            "command_id": command.command_id,
            "command_type": {
                "OrderCommandV2": "submit",
                "CancelOrderCommandV2": "cancel",
                "ReplaceOrderCommandV2": "replace",
            }.get(kind, kind),
            "account_id": command.account_id,
            "order_id": getattr(command, "order_id", None),
            "instrument_id": getattr(command, "instrument_id", None),
            "side": getattr(command, "side", None),
            "order_type": getattr(command, "order_type", None),
            "time_in_force": getattr(command, "time_in_force", None),
            "quantity": getattr(command, "quantity", None),
            "price_ticks": getattr(command, "price_ticks", None),
            "exchange_time_ns": command.exchange_time_ns,
            "venue_sequence": command.venue_sequence,
        }
        for enum_field in ("side", "order_type", "time_in_force"):
            if row[enum_field] is not None:
                row[enum_field] = row[enum_field].value
        self.command_ordinal += 1
        # Streamed straight to the bounded writer: commands are the second
        # largest stream after events, and staging them per episode would let
        # the sink's memory scale with episode length.
        self.writers["exchange_commands"].append(row)
        self.commands_streamed += 1

    def on_events(self, *, episode_id: str, events: Sequence[Any]) -> None:
        rows: list[dict[str, Any]] = []
        for offset, event in enumerate(events):
            rows.append(
                {
                    "corpus_schema_version": CORPUS_SCHEMA_VERSION,
                    "episode_id": episode_id,
                    "event_ordinal": self.events_recorded + offset,
                    "event_id": event.event_id,
                    "kind": event.kind.value,
                    "exchange_time_ns": event.exchange_time_ns,
                    "venue_sequence": event.venue_sequence,
                    "event_priority": event.event_priority,
                    "command_id": event.command_id,
                    "order_id": event.order_id,
                    "payload_json": canonical_json_str(dict(event.payload)),
                }
            )
        self.events_recorded += len(events)
        self.writers["exchange_events"].extend(rows)


# --- the build -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CorpusWriterStats:
    """Build instrumentation: rows, buffers, shards, and throughput."""

    episode_count: int
    row_counts: dict[str, int]
    max_buffered_rows: dict[str, int]
    shard_counts: dict[str, int]
    total_shards: int
    total_bytes: int
    duration_seconds: float
    events_per_second: float


class CorpusManifest:
    """An immutable corpus release's identity (the parsed ``manifest.json``)."""

    def __init__(self, data: dict[str, Any]) -> None:
        if data.get("manifest_version") != CORPUS_RELEASE_INFO_VERSION:
            raise ValueError(f"unsupported manifest version {data.get('manifest_version')!r}")
        self._data = data

    @property
    def data(self) -> dict[str, Any]:
        return dict(self._data)

    @property
    def release_digest(self) -> str:
        return str(self._data["release_digest"])

    @property
    def tables(self) -> dict[str, Any]:
        return dict(self._data.get("tables", {}))


def build_corpus(config: CorpusConfig) -> tuple[CorpusManifest, CorpusWriterStats]:
    """Generate one immutable ``fwf-corpus-v1`` release.

    Refuses to overwrite an existing release; writes into a sibling
    ``.<name>.building`` directory and promotes it atomically only when every
    table sealed and the manifest + dataset card are written. On failure the
    temporary directory is removed and no release directory exists.
    """

    started = time.perf_counter()
    output = config.output
    if output.exists():
        raise FileExistsError(f"refusing to overwrite an existing corpus release: {output}")

    # -- the security order: plan, gate, then anything else --------------------
    plan = config.plan if config.plan is not None else default_training_plan()
    registry = config.registry if config.registry is not None else default_process_registry()
    ecologies = config.ecologies if config.ecologies is not None else default_ecology_registry()
    planned = plan_worlds(
        plan=plan,
        registry=registry,
        ecologies=ecologies,
        count=config.worlds,
        base_seed=config.seed,
    )
    # The gate fires before any world is generated, before any port is
    # constructed, and before the temporary directory or any writer exists.
    # Whole-plan, not requested-prefix: a mixed plan must fail even when only a
    # TRAINABLE prefix would be built.
    require_trainable(tuple({template.split for template in plan.worlds}))
    require_trainable(tuple(world.split for world in planned))

    building = output.parent / f".{output.name}.building"
    output.parent.mkdir(parents=True, exist_ok=True)
    if building.exists():
        # A concurrent or crashed build owns this directory; deleting it would
        # erase a live builder's files. Refuse instead; the caller cleans up.
        raise FileExistsError(
            f"a temporary build directory already exists for {output}; "
            "remove it explicitly if no build is running"
        )
    building.mkdir(parents=True)
    try:
        manifest_data, stats = _build_into(
            building, config=config, plan=plan, planned=planned, registry=registry, started=started
        )
    except BaseException:
        shutil.rmtree(building, ignore_errors=True)
        raise
    os.rename(building, output)
    return CorpusManifest(manifest_data), stats


def _build_into(
    building: Path,
    *,
    config: CorpusConfig,
    plan: EvaluationPlan,
    planned: tuple[PlannedWorld, ...],
    registry: MarketProcessRegistry,
    started: float,
) -> tuple[dict[str, Any], CorpusWriterStats]:
    """The sealed pipeline inside the temporary directory."""

    sessions = _calendar(config.days)
    port_factory = config.port_factory or (lambda index: _policy_port(config.policy, index))

    writers: dict[str, TableWriter] = {}
    for name in table_names():
        writers[name] = TableWriter(
            TABLE_SCHEMAS[name],
            building / name,
            limits=config.limits or WriterLimits(),
        )

    total_events = 0
    try:
        for index, world in enumerate(planned):
            episode_id = episode_id_for(world, task=config.task)
            universe = build_universe(
                universe_id=f"synth-exchange-{config.task.value}-{world.index:04d}",
                planned=world,
                registry=registry,
                security_count=config.securities,
                sessions=sessions,
            )
            task_spec = build_task_spec(
                config.task,
                universe,
                target_quantity=config.target_quantity,
                max_order_quantity=config.max_order_quantity,
            )
            port = port_factory(index)
            sink = _RecordingSink(
                episode_id=episode_id,
                writers=writers,
                snapshot_depth=config.book_depth,
                policy_id=port.name,
            )
            session = BenchmarkSession(
                universe=universe,
                ecology=world.ecology,
                task=task_spec,
                port=port,
                config=SessionConfig(
                    steps_per_day=config.steps_per_day,
                    agent_cash_cents=_agent_cash_cents(config.task),
                ),
                recorder=sink,
                snapshot_depth=config.book_depth,
            )
            try:
                result = session.run()
                outcome = evaluate(task_spec, result)
            finally:
                port.close()
            if not result.scoreable:
                # An unavailable or protocol-invalid agent aborts the release:
                # no invalid episode may enter fwf-corpus-v1.
                raise RuntimeError(
                    f"CORPUS_INVALID_EPISODE: world {world.world_id} was not scoreable "
                    f"(agent_failure={result.agent_failure!r}); the release was aborted"
                )
            manifest_v2 = run_manifest(
                universe=universe, ecology=world.ecology, task=task_spec, port_name=port.name
            )
            _write_universe_rows(writers, episode_id, universe)
            writers["episodes"].append(
                _episode_row(
                    config=config,
                    plan=plan,
                    world=world,
                    universe=universe,
                    result=result,
                    outcome=outcome,
                    manifest_v2=manifest_v2,
                    policy_id=sink.policy_id,
                )
            )
            for row in sink.decision_rows:
                writers["agent_decisions"].append(row)
            for row in sink.snapshot_rows:
                writers["book_snapshots"].append(row)
            # Commands were already streamed into the bounded writer by the
            # sink as they were created; nothing to stage here.
            total_events += result.event_count

        table_entries = [writers[name].close() for name in table_names()]
        max_buffered = {name: writers[name].max_buffered_rows for name in table_names()}
        shard_counts = {name: writers[name].shard_count for name in table_names()}
    finally:
        for writer in writers.values():
            try:
                writer.close()
            except Exception:
                pass  # best-effort close during teardown; sealed entries survive

    release_digest = _release_digest(config=config, plan=plan, table_entries=table_entries)
    manifest_data = _manifest_data(
        config=config,
        plan=plan,
        planned=planned,
        table_entries=table_entries,
        release_digest=release_digest,
        total_events=total_events,
    )
    (building / "manifest.json").write_text(json.dumps(manifest_data, indent=2, sort_keys=True) + "\n")
    (building / "DATASET_CARD.md").write_text(_dataset_card(manifest_data))
    duration = time.perf_counter() - started
    stats = CorpusWriterStats(
        episode_count=len(planned),
        row_counts={
            name: entry["row_count"] for name, entry in zip(table_names(), table_entries, strict=True)
        },
        max_buffered_rows=max_buffered,
        shard_counts=shard_counts,
        total_shards=sum(len(entry["shards"]) for entry in table_entries),
        total_bytes=sum(shard["bytes"] for entry in table_entries for shard in entry["shards"]),
        duration_seconds=round(duration, 3),
        events_per_second=round(total_events / duration, 1) if duration > 0 else 0.0,
    )
    return manifest_data, stats


def _episode_row(
    *,
    config: CorpusConfig,
    plan: EvaluationPlan,
    world: PlannedWorld,
    universe: BenchmarkUniverse,
    result: SessionResult,
    outcome: TaskOutcome,
    manifest_v2: Any,
    policy_id: str,
) -> dict[str, Any]:
    """One ``episodes`` row: the full provenance + outcome of one world."""

    return {
        "corpus_schema_version": CORPUS_SCHEMA_VERSION,
        "episode_id": episode_id_for(world, task=config.task),
        "world_id": world.world_id,
        "world_index": world.index,
        "dataset_split": world.split.value,
        "training_plan_id": plan.plan_id,
        "training_plan_version": plan.version,
        "task": config.task.value,
        "policy_id": policy_id,
        "seed": world.seed,
        "universe_id": universe.universe_id,
        "process_family_id": world.family_id,
        "process_family_digest": world.family_commitment,
        "ecology_id": world.ecology.label,
        "session_count": len(universe.sessions),
        "steps_total": result.steps_total,
        "security_count": len(universe.securities),
        "market_logical_sha256": result.market_logical_sha256,
        "ledger_digest": result.ledger_digest,
        "action_digest": result.action_digest,
        "event_count": result.event_count,
        "trade_count": result.trade_count,
        "order_count": result.order_count,
        "cancel_count": result.cancel_count,
        "replace_count": result.replace_count,
        "score": outcome.score,
        "scoreable": result.scoreable,
        "metrics_json": canonical_json_str({str(key): value for key, value in outcome.metrics.items()}),
        "generator_bundle_digest": manifest_v2.generator_bundle_digest,
        "seed_material_digest": manifest_v2.seed_material_digest,
        # Forward compatibility only: fwf-corpus-v1 defines terminal episode
        # scoring, NOT a dense per-step reward. Always null in v1.
        "reward": None,
    }


def _write_universe_rows(
    writers: dict[str, TableWriter],
    episode_id: str,
    universe: BenchmarkUniverse,
) -> None:
    """Emit securities and daily_bars rows for one episode."""

    for security in universe.securities:
        writers["securities"].append(
            {
                "corpus_schema_version": CORPUS_SCHEMA_VERSION,
                "episode_id": episode_id,
                "symbol": security.symbol,
                "sector": security.sector,
                "initial_price_ticks": security.initial_price_ticks,
                "liquidity": security.liquidity,
                "beta": security.beta,
                "sector_beta": security.sector_beta,
                "drift": security.drift,
                "shares_outstanding": security.shares_outstanding,
            }
        )
        for day_index, session_date in enumerate(universe.sessions):
            writers["daily_bars"].append(
                {
                    "corpus_schema_version": CORPUS_SCHEMA_VERSION,
                    "episode_id": episode_id,
                    "symbol": security.symbol,
                    "session_date": session_date.isoformat(),
                    "open_ticks": security.daily_open_ticks[day_index],
                    "high_ticks": security.daily_high_ticks[day_index],
                    "low_ticks": security.daily_low_ticks[day_index],
                    "close_ticks": security.daily_close_ticks[day_index],
                }
            )


def _release_digest(
    *,
    config: CorpusConfig,
    plan: EvaluationPlan,
    table_entries: list[dict[str, Any]],
) -> str:
    """The corpus-level digest: logical content + generation params only.

    Excludes the output path, wall-clock timestamps, and the temporary directory
    name, so the same logical corpus written elsewhere digest-identifies the
    same release.
    """

    payload = {
        "schema_version": CORPUS_SCHEMA_VERSION,
        "manifest_version": CORPUS_RELEASE_INFO_VERSION,
        "training_plan_id": plan.plan_id,
        "training_plan_version": plan.version,
        "dataset_split": DatasetSplit.TRAINABLE.value,
        "task": config.task.value,
        "policy": config.policy,
        "base_seed": config.seed,
        "world_count": config.worlds,
        "security_count": config.securities,
        "days": config.days,
        "steps_per_day": config.steps_per_day,
        "book_depth": config.book_depth,
        # The policy port NAME varies per episode (an episode index suffix), so
        # the policy word itself is the digest input, not port.name.
        "tables": [
            {
                "name": entry["name"],
                "row_count": entry["row_count"],
                "logical_sha256": entry["logical_sha256"],
            }
            for entry in table_entries
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_data(
    *,
    config: CorpusConfig,
    plan: EvaluationPlan,
    planned: tuple[PlannedWorld, ...],
    table_entries: list[dict[str, Any]],
    release_digest: str,
    total_events: int,
) -> dict[str, Any]:
    """The complete ``manifest.json`` document."""

    family_ids = sorted({world.family_id for world in planned})
    commitments = {world.family_id: world.family_commitment for world in planned}
    visibility = {world.family_id: world.family_visibility.value for world in planned}
    commit = _git_commit() if config.record_git_provenance else None
    return {
        "manifest_version": CORPUS_RELEASE_INFO_VERSION,
        "corpus_schema_version": CORPUS_SCHEMA_VERSION,
        "release_digest": release_digest,
        "dataset_split": DatasetSplit.TRAINABLE.value,
        "training_plan_id": plan.plan_id,
        "training_plan_version": plan.version,
        "task": config.task.value,
        "policy": config.policy,
        "base_seed": config.seed,
        "world_count": config.worlds,
        "security_count": config.securities,
        "days": config.days,
        "steps_per_day": config.steps_per_day,
        "book_depth": config.book_depth,
        "episode_count": len(planned),
        "total_events": total_events,
        "process_family_ids": family_ids,
        "process_family_commitments": commitments,
        "process_family_visibility": visibility,
        "ecology_ids": sorted({world.ecology.label for world in planned}),
        # The anti-leakage statement, machine-checkable by the validator.
        "synthetic_only": True,
        "contains_historical_price_paths": False,
        "contains_real_market_data": False,
        "contains_sealed_evaluation_data": False,
        "tables": [dict(entry) for entry in table_entries],
        "generator": {
            "package": "synthetic-market-world",
            "corpus_module": "app.corpus",
            "fwf_version": _package_version(),
            "pyarrow_version": _pyarrow_version(),
            "git_commit": commit,
        },
    }


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("synthetic-market-world")
    except Exception:
        return "unknown"


def _pyarrow_version() -> str:
    import pyarrow

    return pyarrow.__version__


def _dataset_card(manifest: dict[str, Any]) -> str:
    """The generated ``DATASET_CARD.md`` (deterministic metadata only)."""

    tables = manifest["tables"]
    table_lines = "\n".join(
        f"| {entry['name']} | {entry['schema_version']} | {entry['grain']} | {entry['row_count']:,} "
        f"| `{entry['logical_sha256'][:16]}...` |"
        for entry in tables
    )
    total_rows = sum(entry["row_count"] for entry in tables)
    return f"""# Dataset Card: {manifest["training_plan_id"]} ({manifest["corpus_schema_version"]})

A leakage-safe, fully synthetic financial training corpus generated by
Financial World Factory (M10.7).

## What this corpus contains

Synthetic securities, daily OHLC bars, a limit-order-book exchange's canonical
commands and events, reference-policy agent decisions with their exact
observations and actions, decision-time aggregate L{manifest["book_depth"]} book
snapshots, and the terminal task score of each episode. Every row is generated
by FWF's deterministic synthetic engine; nothing here derives from real market
data.

## Generation parameters

| field | value |
| --- | --- |
| corpus schema | `{manifest["corpus_schema_version"]}` |
| training plan | `{manifest["training_plan_id"]}` ({manifest["training_plan_version"]}) |
| dataset split | `{manifest["dataset_split"]}` (every world) |
| task | `{manifest["task"]}` |
| policy | `{manifest["policy"]}` |
| base seed | {manifest["base_seed"]} |
| worlds | {manifest["world_count"]} |
| securities per world | {manifest["security_count"]} |
| days | {manifest["days"]} |
| steps per day | {manifest["steps_per_day"]} |
| book depth | {manifest["book_depth"]} levels |
| episodes | {manifest["episode_count"]} |
| total exchange events | {manifest["total_events"]:,} |

## Process families

{", ".join(f"`{item}`" for item in manifest["process_family_ids"])} (all
**public**; family commitments are recorded in `manifest.json`). Ecologies:
{", ".join(f"`{item}`" for item in manifest["ecology_ids"])}.

## Tables

| table | ver | grain | rows | logical hash (prefix) |
| --- | --- | --- | ---: | --- |
{table_lines}

Total rows: {total_rows:,}. Logical hashes are independent SHA-256 commitments
over each table's canonical rows in order; the validator recomputes them from
the Parquet data on every read.

## Intended uses

* Training execution / trading agents on synthetic limit-order-book
  environments with exactly replayable provenance.
* Research on distribution shift across known generator families and ecologies.
* Regression fixtures: a release is immutable and hash-committed.

## Anti-leakage guarantees

* Every world is declared ``TRAINABLE`` by an explicit, validated plan; worlds
  marked ``PUBLIC_EVAL`` or ``SEALED_EVAL`` are structurally refused by the
  build path **before any generation happens**.
* ``SEALED_EVAL`` worlds come only from evaluator-private process families;
  such families can never be named by the published training plan, and the
  build rejects any non-public family outright.
* The manifest asserts `contains_sealed_evaluation_data = false`, and the
  validator re-checks every persisted row for leakage markers.
* `release_digest` commits to schema, plan, generation parameters, table
  logical hashes, and row counts -- not to paths or timestamps.

## Known limitations

* **No real historical price paths.** The corpus is synthetic-only by
  construction; do not use it as a calibration source for real-market
  statistics.
* **Background agents are interpretable archetypes**, not a fully calibrated
  institutional participant population. Synthetic microstructure is
  plausible, not empirical.
* **`exchange_time_ns` is a deterministic logical clock**, an ordered logical
  timestamp in the current benchmark kernel. M10.7 does not yet model
  real wall-clock exchange or network latency.
* **Book snapshots are decision-time L{manifest["book_depth"]} snapshots**,
  captured immediately before each recorded agent decision -- not a snapshot
  after every exchange event.
* **The exchange is the Python reference implementation** (`MatchingExchangeV2`),
  not the eventually accelerated 100k-security engine.
* **Scores are terminal episode outcomes**, not dense RL rewards. The
  `reward` column exists for forward compatibility and is always null in
  `fwf-corpus-v1`.
"""
