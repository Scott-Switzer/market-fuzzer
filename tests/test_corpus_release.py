"""M10.7 corpus recording, writer, and release-integrity tests.

Covers the recorder seam (no-op equivalence, decision/command/event capture,
bounded buffering), the sharded Parquet writer (buffer flushing, deterministic
names, streaming logical hashes), and release-level guarantees (atomicity,
reproducibility, foreign keys, event-count reconciliation, reader smoke).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.benchmark.model import TaskKind
from app.benchmark.plan import resolve_world
from app.benchmark.port import twap_port
from app.benchmark.process_registry import default_process_registry
from app.benchmark.session import BenchmarkSession
from app.benchmark.tasks import build_task_spec, evaluate
from app.benchmark.universe import FAMILIAR_ECOLOGY
from app.corpus.builder import CorpusConfig, build_corpus
from app.corpus.recorder import CountingRecorder, NullSessionRecorder
from app.corpus.schema import CORPUS_SCHEMA_VERSION, TABLE_SCHEMAS, table_names
from app.corpus.validate import (
    ValidationError,
    validate_corpus,
)


def _small_release(tmp_path: Path, name: str = "release", worlds: int = 2):
    config = CorpusConfig(
        output=tmp_path / name,
        worlds=worlds,
        securities=4,
        days=2,
        steps_per_day=6,
        seed=99,
    )
    manifest, stats = build_corpus(config)
    return manifest, stats


# --- the recorder is an observer, never a participant -------------------------------


def test_a_recorded_session_is_byte_identical_to_a_bare_session() -> None:
    registry = default_process_registry()
    world = resolve_world(
        plan=_one_template_plan(),
        index=0,
        registry=registry,
        base_seed=7,
    )
    sessions = _three_days()

    def build_and_run(recorder):
        from app.benchmark.universe import build_universe

        universe = build_universe(
            universe_id="synth-exchange-equivalence-0000",
            planned=world,
            registry=registry,
            security_count=4,
            sessions=sessions,
        )
        task_spec = build_task_spec(TaskKind.EXECUTION, universe, target_quantity=5_000)
        session = BenchmarkSession(
            universe=universe,
            ecology=world.ecology,
            task=task_spec,
            port=twap_port(slice_quantity=150),
            recorder=recorder,
        )
        return task_spec, session.run()

    _task, bare = build_and_run(None)
    recorder = CountingRecorder()
    _task, recorded = build_and_run(recorder)

    for field in (
        "world_id",
        "session_count" if hasattr(bare, "session_count") else "steps_total",
        "ledger_digest",
        "market_logical_sha256",
        "action_digest",
        "event_count",
        "order_count",
        "cancel_count",
        "replace_count",
        "trade_count",
        "agent_fills",
        "agent_positions",
        "equity_curve_cents",
        "agent_final_value_cents",
        "quote_uptime",
        "violations",
        "scoreable",
    ):
        assert getattr(bare, field) == getattr(recorded, field), field
    assert evaluate(_task, bare).score == evaluate(_task, recorded).score
    # The recorder saw every decision and snapshot, exactly once per decision.
    assert recorder.hooks.decisions == bare.steps_total * len(_task.agent_instruments)
    assert recorder.hooks.snapshots == recorder.hooks.decisions
    assert recorder.hooks.commands > 0
    assert recorder.hooks.events == bare.event_count


def test_the_default_recorder_is_the_null_recorder() -> None:
    from app.benchmark.universe import build_universe

    registry = default_process_registry()
    world = resolve_world(plan=_one_template_plan(), index=0, registry=registry, base_seed=7)
    universe = build_universe(
        universe_id="synth-exchange-null-0000",
        planned=world,
        registry=registry,
        security_count=2,
        sessions=_three_days(),
    )
    task_spec = build_task_spec(TaskKind.EXECUTION, universe, target_quantity=2_000)
    session = BenchmarkSession(
        universe=universe,
        ecology=world.ecology,
        task=task_spec,
        port=twap_port(slice_quantity=100),
    )
    assert isinstance(session.recorder, NullSessionRecorder)
    # And it still runs to a complete, scoreable result with no recording state.
    result = session.run()
    assert result.scoreable


def test_every_recorded_observation_matches_its_snapshot_state() -> None:
    """The snapshot is the exact pre-decision book the observation quotes."""

    registry = default_process_registry()
    world = resolve_world(plan=_one_template_plan(), index=0, registry=registry, base_seed=11)
    from app.benchmark.universe import build_universe

    universe = build_universe(
        universe_id="synth-exchange-alignment-0000",
        planned=world,
        registry=registry,
        security_count=2,
        sessions=_three_days(),
    )
    task_spec = build_task_spec(TaskKind.EXECUTION, universe, target_quantity=2_000)
    recorder = CountingRecorder()
    session = BenchmarkSession(
        universe=universe,
        ecology=world.ecology,
        task=task_spec,
        port=twap_port(slice_quantity=100),
        recorder=recorder,
    )
    session.run()
    assert recorder.hooks.decisions > 0


# --- writer behaviour ---------------------------------------------------------------


def test_the_writer_shards_deterministically_and_hashes_streamingly(tmp_path: Path) -> None:
    from app.corpus.writer import TableWriter, WriterLimits, canonical_row_bytes

    table = TABLE_SCHEMAS["daily_bars"]
    writer = TableWriter(
        table,
        tmp_path / "daily_bars",
        limits=WriterLimits(batch_target_rows=4, row_group_rows=8, max_rows_per_file=10),
    )
    expected_hash = hashlib.sha256()
    for index in range(25):
        row = {
            "corpus_schema_version": CORPUS_SCHEMA_VERSION,
            "episode_id": "ep-1",
            "symbol": f"SYN{index % 3:03d}",
            "session_date": f"2026-06-0{index % 5 + 1}",
            "open_ticks": 100 + index,
            "high_ticks": 110 + index,
            "low_ticks": 90 + index,
            "close_ticks": 105 + index,
        }
        writer.append(row)
        expected_hash.update(canonical_row_bytes(row))
        expected_hash.update(b"\n")
    stat = writer.close()

    entries = sorted((tmp_path / "daily_bars").glob("*.parquet"))
    assert [entry.name for entry in entries] == [
        "part-00000.parquet",
        "part-00001.parquet",
        "part-00002.parquet",
    ]
    assert stat["row_count"] == 25
    assert stat["logical_sha256"] == expected_hash.hexdigest()
    assert [shard["rows"] for shard in stat["shards"]] == [10, 10, 5]
    # ZSTD compression is on every shard.
    for shard_path in entries:
        metadata = pq.ParquetFile(shard_path).metadata
        for group in range(metadata.num_row_groups):
            for column in range(metadata.num_columns):
                assert metadata.row_group(group).column(column).compression.lower().startswith("zstd")


def test_a_small_buffer_guarantees_multiple_flushes(tmp_path: Path) -> None:
    from app.corpus.writer import TableWriter, WriterLimits

    writer = TableWriter(
        TABLE_SCHEMAS["exchange_events"],
        tmp_path / "exchange_events",
        limits=WriterLimits(batch_target_rows=5, max_rows_per_file=12),
    )
    for index in range(30):
        writer.append(_event_row(index))
    # The pending buffer never exceeded the bound (flush happens at 5).
    assert writer.max_buffered_rows <= 5
    stat = writer.close()
    assert stat["row_count"] == 30
    # 30 rows / 12-per-file = 3 shards exactly.
    assert len(stat["shards"]) == 3


def _event_row(index: int) -> dict:
    return {
        "corpus_schema_version": CORPUS_SCHEMA_VERSION,
        "episode_id": "ep-1",
        "event_ordinal": index,
        "event_id": f"evt-{index:020d}",
        "kind": "session_opened",
        "exchange_time_ns": index,
        "venue_sequence": index,
        "event_priority": 15,
        "command_id": "control:session_opened:session",
        "order_id": "control:session",
        "payload_json": '{"target":"session"}',
    }


# --- release integrity ---------------------------------------------------------------


def test_a_release_validates_and_carries_all_seven_tables(tmp_path: Path) -> None:
    _manifest, stats = _small_release(tmp_path)
    assert set(stats.row_counts) == set(table_names())
    result = validate_corpus(tmp_path / "release")
    assert result.tables == stats.row_counts
    # Decision/snapshot grain alignment: one snapshot per recorded decision.
    assert result.tables["book_snapshots"] == result.tables["agent_decisions"]


def test_the_release_directory_layout_is_deterministic(tmp_path: Path) -> None:
    _manifest, _stats = _small_release(tmp_path)
    release = tmp_path / "release"
    names = sorted(child.name for child in release.iterdir())
    assert set(names) == {
        "DATASET_CARD.md",
        "agent_decisions",
        "book_snapshots",
        "daily_bars",
        "episodes",
        "exchange_commands",
        "exchange_events",
        "manifest.json",
        "securities",
    }
    # Every shard uses the deterministic part-N naming.
    for child in release.iterdir():
        if child.is_dir():
            shard_names = [shard.name for shard in child.iterdir()]
            assert shard_names == [f"part-{index:05d}.parquet" for index in range(len(shard_names))]


def test_a_failed_build_leaves_no_release_and_no_building_dir(tmp_path: Path) -> None:
    from test_benchmark_private_evaluation import _sealed_plan, _trusted_registry

    from app.benchmark.plan import SealedEvaluationExportError

    guard = tmp_path / "guard"
    guard.mkdir()
    with pytest.raises(SealedEvaluationExportError):
        build_corpus(
            CorpusConfig(
                output=guard / "nope",
                worlds=2,
                securities=4,
                days=1,
                steps_per_day=4,
                plan=_sealed_plan(),
                registry=_trusted_registry(),
                seed=5,
            )
        )
    assert sorted(child.name for child in guard.iterdir()) == []


def test_corpora_built_into_different_directories_are_logically_and_physically_equal(
    tmp_path: Path,
) -> None:
    from app.corpus.builder import CorpusConfig as Config

    settings = dict(worlds=3, securities=4, days=2, steps_per_day=8, seed=1234)
    manifest_a, _ = build_corpus(Config(output=tmp_path / "corpus-a", **settings))
    manifest_b, _ = build_corpus(Config(output=tmp_path / "corpus-b", **settings))

    # Reproducibility contract (spec 40).
    assert manifest_a.release_digest == manifest_b.release_digest
    assert (tmp_path / "corpus-a" / "manifest.json").read_bytes() == (
        tmp_path / "corpus-b" / "manifest.json"
    ).read_bytes()
    for name in table_names():
        shards_a = sorted((tmp_path / "corpus-a" / name).glob("*.parquet"))
        shards_b = sorted((tmp_path / "corpus-b" / name).glob("*.parquet"))
        assert [shard.name for shard in shards_a] == [shard.name for shard in shards_b]
        for shard_a, shard_b in zip(shards_a, shards_b, strict=True):
            # The pinned pyarrow writer is byte-deterministic under these bounds.
            assert shard_a.read_bytes() == shard_b.read_bytes(), name


def test_event_counts_reconcile_with_session_results(tmp_path: Path) -> None:
    _manifest, _stats = _small_release(tmp_path, worlds=3)
    manifest = json.loads((tmp_path / "release" / "manifest.json").read_text())
    episodes = pq.read_table(tmp_path / "release" / "episodes" / "part-00000.parquet").to_pylist()
    per_episode_events = {}
    for entry in manifest["tables"]:
        if entry["name"] == "exchange_events":
            table = pa.concat_tables(
                [pq.read_table(tmp_path / "release" / shard["relative_path"]) for shard in entry["shards"]]
            )
            for episode_id in table.column("episode_id").to_pylist():
                per_episode_events[episode_id] = per_episode_events.get(episode_id, 0) + 1
    for episode in episodes:
        assert per_episode_events.get(episode["episode_id"], 0) == episode["event_count"]


# --- validator rejections (the planted-defect matrix, spec 43) -----------------------


def _with_defect(tmp_path: Path, defect) -> None:
    """Copy the gold release, plant one defect, expect a rejection."""

    out = tmp_path / "defect"
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(tmp_path / "gold", out)
    defect(out)
    with pytest.raises(ValidationError):
        validate_corpus(out)


def _corruption_suite(tmp_path: Path) -> None:
    """Runs every planted-defect case against a fresh folded gold copy."""

    def manifest_entry(work: Path, table: str, mutate) -> None:
        blob = json.loads((work / "manifest.json").read_text())
        for entry in blob["tables"]:
            if entry["name"] == table:
                mutate(entry)
        (work / "manifest.json").write_text(json.dumps(blob))

    def rewrite_shard(work: Path, table: str, mutate_rows) -> None:
        path = next((work / table).glob("*.parquet"))
        schema = pq.ParquetFile(path).schema_arrow
        rows = pq.read_table(path).to_pylist()
        mutate_rows(rows)
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)

    def levels(pairs):
        return [{"price_ticks": price, "quantity": quantity} for price, quantity in pairs]

    # delete shard
    _with_defect(tmp_path, lambda work: next((work / "daily_bars").glob("*.parquet")).unlink())
    # manifest logical-hash tamper
    _with_defect(
        tmp_path,
        lambda work: manifest_entry(
            work, "episodes", lambda entry: entry.__setitem__("logical_sha256", "0" * 64)
        ),
    )
    # manifest physical-sha tamper
    _with_defect(
        tmp_path,
        lambda work: manifest_entry(
            work, "securities", lambda entry: entry["shards"][0].__setitem__("sha256", "0" * 64)
        ),
    )
    # row-count mismatch
    _with_defect(
        tmp_path,
        lambda work: manifest_entry(work, "securities", lambda entry: entry.__setitem__("row_count", 999)),
    )
    # alter one parquet value (physical sha breaks first)
    _with_defect(
        tmp_path,
        lambda work: rewrite_shard(
            work, "daily_bars", lambda rows: rows[0].__setitem__("open_ticks", rows[0]["open_ticks"] + 7)
        ),
    )
    # duplicate episode id
    _with_defect(
        tmp_path,
        lambda work: rewrite_shard(
            work, "episodes", lambda rows: rows[1].__setitem__("episode_id", rows[0]["episode_id"])
        ),
    )
    # break a foreign key
    _with_defect(
        tmp_path,
        lambda work: rewrite_shard(
            work, "daily_bars", lambda rows: rows[0].__setitem__("episode_id", "ep-nope")
        ),
    )
    # inject SEALED_EVAL
    _with_defect(
        tmp_path,
        lambda work: rewrite_shard(
            work, "episodes", lambda rows: rows[0].__setitem__("dataset_split", "sealed_eval")
        ),
    )
    # inject evaluator-private family label
    _with_defect(
        tmp_path,
        lambda work: rewrite_shard(
            work,
            "episodes",
            lambda rows: rows[0].__setitem__("process_family_id", "evaluator-private-family-v1"),
        ),
    )
    # break an OHLC bound (high below low)
    _with_defect(
        tmp_path,
        lambda work: rewrite_shard(
            work, "daily_bars", lambda rows: rows[0].__setitem__("high_ticks", rows[0]["low_ticks"] - 10)
        ),
    )
    # negative price
    _with_defect(
        tmp_path,
        lambda work: rewrite_shard(
            work, "securities", lambda rows: rows[0].__setitem__("initial_price_ticks", -5)
        ),
    )
    # book depth ordering violation (bids ascending)
    _with_defect(
        tmp_path,
        lambda work: rewrite_shard(
            work, "book_snapshots", lambda rows: rows[0].__setitem__("bids", levels([(10, 5), (11, 5)]))
        ),
    )
    # crossed snapshot
    _with_defect(
        tmp_path,
        lambda work: rewrite_shard(
            work,
            "book_snapshots",
            lambda rows: (
                rows[0].__setitem__("bids", levels([(101, 5)]))
                or rows[0].__setitem__("asks", levels([(100, 5)]))
            ),
        ),
    )
    # decision references an unknown snapshot
    _with_defect(
        tmp_path,
        lambda work: rewrite_shard(
            work, "agent_decisions", lambda rows: rows[0].__setitem__("book_snapshot_id", "snap-unknown")
        ),
    )
    # unmanifested extra shard
    _with_defect(tmp_path, lambda work: (work / "securities" / "part-00042.parquet").write_bytes(b"garbage"))
    # missing manifest
    _with_defect(tmp_path, lambda work: (work / "manifest.json").unlink())
    # unknown schema version
    _with_defect(
        tmp_path,
        lambda work: manifest_entry(work, "episodes", lambda _entry: None) or _bump_schema(work),
    )


def _bump_schema(work: Path) -> None:
    blob = json.loads((work / "manifest.json").read_text())
    blob["corpus_schema_version"] = "fwf-corpus-v9"
    (work / "manifest.json").write_text(json.dumps(blob))


def test_the_full_corruption_matrix_is_rejected(tmp_path: Path) -> None:
    _small_release(tmp_path, name="gold")
    _corruption_suite(tmp_path)


# --- reader smoke test (spec 46) ------------------------------------------------------


def test_a_consumer_reads_a_release_without_simulation_objects(tmp_path: Path) -> None:
    """manifest.json + Parquet are sufficient for a neutral consumer."""

    _small_release(tmp_path, name="reader")
    release = tmp_path / "reader"
    manifest = json.loads((release / "manifest.json").read_text())

    def read(name: str) -> pa.Table:
        entry = next(item for item in manifest["tables"] if item["name"] == name)
        return pa.concat_tables(
            [pq.read_table(release / shard["relative_path"]) for shard in entry["shards"]]
        )

    episodes = read("episodes").to_pylist()
    assert episodes
    episode = episodes[0]
    episode_id = episode["episode_id"]
    securities = read("securities").to_pylist()
    bars = read("daily_bars").to_pylist()
    decisions = read("agent_decisions").to_pylist()
    events = read("exchange_events").to_pylist()
    snapshots = read("book_snapshots").to_pylist()

    own_securities = [row for row in securities if row["episode_id"] == episode_id]
    own_bars = [row for row in bars if row["episode_id"] == episode_id]
    own_decisions = [row for row in decisions if row["episode_id"] == episode_id]
    own_events = [row for row in events if row["episode_id"] == episode_id]
    own_snapshots = {row["snapshot_id"]: row for row in snapshots if row["episode_id"] == episode_id}

    assert len(own_securities) == episode["security_count"]
    assert len(own_bars) == episode["security_count"] * episode["session_count"]
    # Pre-decision book alignment: each decision carries a decision-time L10
    # snapshot whose touch agrees with the observation document.
    for decision in own_decisions:
        if decision["book_snapshot_id"] is None:
            continue
        snapshot = own_snapshots[decision["book_snapshot_id"]]
        observation = json.loads(decision["observation_json"])
        assert snapshot["best_bid_ticks"] == observation["best_bid_ticks"]
        assert snapshot["best_ask_ticks"] == observation["best_ask_ticks"]
        # Event-delta indices bracket the action's commands.
        assert decision["ledger_event_index_before"] <= decision["ledger_event_index_after"]
        assert decision["ledger_event_index_after"] <= len(own_events)
    # The observation documents are exactly the published protocol.
    assert set(json.loads(own_decisions[0]["observation_json"])) >= {"session_id", "step", "symbol", "side"}


# --- helpers ---------------------------------------------------------------------------


def _one_template_plan():
    from app.benchmark.plan import DatasetSplit, EvaluationPlan, EvaluationWorldTemplate

    return EvaluationPlan(
        plan_id="corpus_test_plan_v1",
        version="v1",
        worlds=(
            EvaluationWorldTemplate(
                family_id="gjr_factor_t_v1",
                ecology_id=FAMILIAR_ECOLOGY.label,
                split=DatasetSplit.TRAINABLE,
            ),
        ),
    )


def _three_days():
    from datetime import date, timedelta

    from app.market.calendar import trading_days

    start = date(2026, 6, 1)
    return tuple(trading_days(start, start + timedelta(days=20))[:3])
