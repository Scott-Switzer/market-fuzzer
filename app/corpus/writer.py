"""Bounded, deterministic Parquet shard writer (M10.7).

One :class:`BoundedShardWriter` per release table:

* rows are appended into an in-memory Arrow buffer flushed at
  ``batch_target_rows`` (default 10,000), so memory scales with the buffer plus
  one world, never with corpus size;
* a shard file rolls over at ``max_rows_per_file`` (default 250,000 rows) under
  deterministic names ``part-00000.parquet``, ``part-00001.parquet``, ...;
* each shard is ZSTD-compressed Parquet with an explicit schema and a fixed row
  group target (default 50,000);
* the table's ``logical_sha256`` is a *streaming* SHA-256 over canonical rows in
  append order -- computed progressively, never re-read or re-derived at close.

The canonical row form hashed here is exactly what the validator recomputes from
the Parquet files: sorted-key compact JSON of the row's Python values, newline
joined. Logical content identity is therefore format-independent, while each
shard additionally carries its own physical ``sha256`` and byte count in the
manifest inventory.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from app.corpus.schema import CorpusTable

__all__ = [
    "DEFAULT_BATCH_TARGET_ROWS",
    "DEFAULT_MAX_ROWS_PER_FILE",
    "DEFAULT_ROW_GROUP_ROWS",
    "ArrowRowBuilder",
    "ShardStat",
    "TableWriter",
    "WriterLimits",
]

DEFAULT_BATCH_TARGET_ROWS = 10_000
DEFAULT_ROW_GROUP_ROWS = 50_000
DEFAULT_MAX_ROWS_PER_FILE = 250_000

#: ZSTD level: strong compression, stable across the pinned pyarrow version.
_ZSTD_LEVEL = 3


@dataclass(frozen=True, slots=True)
class WriterLimits:
    """The buffer/shard bounds a release was written under."""

    batch_target_rows: int = DEFAULT_BATCH_TARGET_ROWS
    row_group_rows: int = DEFAULT_ROW_GROUP_ROWS
    max_rows_per_file: int = DEFAULT_MAX_ROWS_PER_FILE

    def __post_init__(self) -> None:
        if min(self.batch_target_rows, self.row_group_rows, self.max_rows_per_file) < 1:
            raise ValueError("writer limits must be positive")
        if self.batch_target_rows > self.max_rows_per_file:
            raise ValueError("batch target cannot exceed the per-file row bound")


@dataclass(frozen=True, slots=True)
class ShardStat:
    """Physical inventory for one written shard file."""

    relative_path: str
    rows: int
    bytes: int
    sha256: str


def canonical_row_bytes(values: dict[str, Any]) -> bytes:
    """The canonical row encoding hashed into ``logical_sha256``.

    Deterministic compact JSON with sorted keys. Lists (depth levels) become
    JSON arrays; ``None`` stays JSON ``null``. The validator recomputes exactly
    this from the round-tripped rows.
    """

    return json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode(
        "utf-8"
    )


class ArrowRowBuilder:
    """Accumulates typed rows into a per-table Arrow record batch buffer."""

    def __init__(self, schema: pa.Schema, *, batch_target_rows: int) -> None:
        self.schema = schema
        self.batch_target_rows = batch_target_rows
        self._columns: dict[str, list[Any]] = {field.name: [] for field in schema}
        self._rows = 0
        self.max_buffered_rows = 0

    def append(self, row: dict[str, Any]) -> None:
        for field in self.schema:
            if field.name not in row:
                raise KeyError(f"row for {field.name!r} is missing from {sorted(row)}")
        for name, value in row.items():
            self._columns[name].append(value)
        self._rows += 1
        if self._rows > self.max_buffered_rows:
            self.max_buffered_rows = self._rows

    def pending(self) -> int:
        return self._rows

    def take_batch(self) -> pa.RecordBatch | None:
        """Drain the buffer into one record batch (``None`` when empty)."""

        if self._rows == 0:
            return None
        arrays = []
        for field in self.schema:
            arrays.append(pa.array(self._columns[field.name], type=field.type))
            self._columns[field.name] = []
        batch = pa.RecordBatch.from_arrays(arrays, schema=self.schema)
        self._rows = 0
        return batch

    def rollback_last_row(self) -> None:
        """Drop the most recently appended row (used by the split-point logic)."""

        if self._rows == 0:
            return
        for name in self._columns:
            self._columns[name].pop()
        self._rows -= 1


class TableWriter:
    """One release table: buffered appends, sharded files, streaming hash.

    Row groups: the Arrow builder buffers to ``batch_target_rows`` and this
    writer accumulates consecutive batches up to ``row_group_rows`` before
    emitting a single Parquet row group, so the declared grouping is actually
    built instead of silently following the batch size.
    """

    def __init__(
        self,
        table: CorpusTable,
        directory: Path,
        *,
        limits: WriterLimits | None = None,
    ) -> None:
        self.table = table
        self.directory = directory
        self.limits = limits or WriterLimits()
        self.builder = ArrowRowBuilder(table.schema, batch_target_rows=self.limits.batch_target_rows)
        self._hash = hashlib.sha256()
        self._rows_total = 0
        self._shard_index = 0
        self._shard_rows = 0
        self._shards: list[ShardStat] = []
        self._writer: pq.ParquetWriter | None = None
        self._shard_path: Path | None = None
        self._pending_group: list[pa.RecordBatch] = []
        self._pending_group_rows = 0
        directory.mkdir(parents=True, exist_ok=True)

    # -- row ingestion -----------------------------------------------------------

    def append(self, row: dict[str, Any]) -> None:
        """Buffer one row; flush/shard-roll happens as bounds are reached."""

        self.builder.append(row)
        self._hash.update(canonical_row_bytes(row))
        self._hash.update(b"\n")
        self._rows_total += 1
        if self.builder.pending() >= self.limits.batch_target_rows:
            self.flush_buffer()

    def extend(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            self.append(row)

    # -- flushing ----------------------------------------------------------------

    def _open_shard(self) -> None:
        self._shard_path = self.directory / f"part-{self._shard_index:05d}.parquet"
        self._writer = pq.ParquetWriter(
            self._shard_path,
            self.table.schema,
            compression="zstd",
            compression_level=_ZSTD_LEVEL,
            # Explicit so the pinned writer's row grouping is reproducible.
            version="2.6",
            write_statistics=True,
        )
        self._shard_rows = 0

    def flush_buffer(self) -> None:
        if self.builder.pending() == 0:
            return
        batch = self.builder.take_batch()
        assert batch is not None
        self._write_batch(batch)

    def _staged_group_rows(self) -> int:
        return sum(batch.num_rows for batch in self._pending_group) + self._pending_group_rows

    def _write_batch(self, batch: pa.RecordBatch) -> None:
        """Stage one flushed batch into the row-group accumulator."""

        self._pending_group.append(batch)
        self._pending_group_rows += batch.num_rows
        if self._staged_group_rows() >= self.limits.row_group_rows:
            self._flush_row_group()

    def _flush_row_group(self) -> None:
        """Emit the staged batches as one row group (split across shards as needed)."""

        if not self._pending_group:
            return
        group = pa.Table.from_batches(self._pending_group, schema=self.table.schema)
        self._pending_group = []
        self._pending_group_rows = 0
        rows_to_write = group.num_rows
        offset = 0
        while rows_to_write > 0:
            if self._writer is None or self._shard_rows >= self.limits.max_rows_per_file:
                self._close_shard()
                self._open_shard()
            space = self.limits.max_rows_per_file - self._shard_rows
            take = min(rows_to_write, space)
            piece = group.slice(offset, take)
            assert self._writer is not None
            self._writer.write_table(piece)
            self._shard_rows += take
            offset += take
            rows_to_write -= take

    def _close_shard(self) -> None:
        if self._writer is None or self._shard_path is None:
            return
        self._writer.close()
        data = self._shard_path.read_bytes()
        self._shards.append(
            ShardStat(
                relative_path=f"{self.table.name}/part-{self._shard_index:05d}.parquet",
                rows=self._shard_rows,
                bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )
        self._writer = None
        self._shard_path = None
        self._shard_index += 1
        self._shard_rows = 0

    # -- completion ---------------------------------------------------------------

    def close(self) -> dict[str, Any]:
        """Flush, seal the last shard, and return the table's manifest entry."""

        self.flush_buffer()
        self._flush_row_group()
        self._close_shard()
        return {
            "name": self.table.name,
            "schema_version": self.table.schema_version,
            "grain": self.table.grain,
            "row_count": self._rows_total,
            "logical_sha256": self._hash.hexdigest(),
            "shards": [
                {
                    "relative_path": shard.relative_path,
                    "rows": shard.rows,
                    "bytes": shard.bytes,
                    "sha256": shard.sha256,
                }
                for shard in self._shards
            ],
        }

    @property
    def rows_total(self) -> int:
        return self._rows_total

    @property
    def max_buffered_rows(self) -> int:
        return self.builder.max_buffered_rows

    @property
    def shard_count(self) -> int:
        return len(self._shards)
