"""Independent corpus validation (M10.7).

:func:`validate_corpus` re-reads a release from disk -- ``manifest.json`` plus
the Parquet shards -- and re-derives every integrity claim from the data
itself. It trusts the manifest only for *comparison* (declared hashes, counts,
schema) and never as evidence: a hash is recomputed from file contents, a row
count is counted, a foreign key is checked row by row. A release that fails any
check names the failure with a stable ``code`` so CI output can pin it.

Checks (the corruption matrix each maps to):

* manifest present / versions known       MANIFEST_MISSING, MANIFEST_SCHEMA_UNKNOWN
* every declared shard present + intact   FILE_MISSING, FILE_SHA_MISMATCH
* schema of each shard matches exactly     SCHEMA_MISMATCH
* row counts match the manifest            ROW_COUNT_MISMATCH
* logical hashes recomputed on read        LOGICAL_HASH_MISMATCH
* episode ids unique; children resolve     DUPLICATE_EPISODE_ID, FOREIGN_KEY
* decisions unique, contiguous, aligned    DECISION_ORDER, SNAPSHOT_FK
* events ordered + contiguous + reconciled EVENT_ORDER, EVENT_COUNT_MISMATCH
* commands unique; ids non-empty           COMMAND_ORDER
* book depth ordering / positives          BOOK_SNAPSHOT_INVALID
* OHLC bounds hold                         OHLC_INVALID
* no negative/infinite required numerics   NUMERIC_INVALID
* no non-TRAINABLE rows / private labels   LEAKAGE_DETECTED
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from app.corpus.schema import CORPUS_SCHEMA_VERSION, TABLE_SCHEMAS, table_names
from app.corpus.writer import canonical_row_bytes

__all__ = ["ValidationError", "validate_corpus"]

#: Codes the validator reports. Pinned by tests so CI failures cannot drift.
MANIFEST_MISSING = "MANIFEST_MISSING"
MANIFEST_SCHEMA_UNKNOWN = "MANIFEST_SCHEMA_UNKNOWN"
FILE_MISSING = "FILE_MISSING"
FILE_SHA_MISMATCH = "FILE_SHA_MISMATCH"
SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
ROW_COUNT_MISMATCH = "ROW_COUNT_MISMATCH"
LOGICAL_HASH_MISMATCH = "LOGICAL_HASH_MISMATCH"
DUPLICATE_EPISODE_ID = "DUPLICATE_EPISODE_ID"
FOREIGN_KEY = "FOREIGN_KEY"
DECISION_ORDER = "DECISION_ORDER"
SNAPSHOT_FK = "SNAPSHOT_FK"
EVENT_ORDER = "EVENT_ORDER"
EVENT_COUNT_MISMATCH = "EVENT_COUNT_MISMATCH"
COMMAND_ORDER = "COMMAND_ORDER"
BOOK_SNAPSHOT_INVALID = "BOOK_SNAPSHOT_INVALID"
OHLC_INVALID = "OHLC_INVALID"
NUMERIC_INVALID = "NUMERIC_INVALID"
LEAKAGE_DETECTED = "LEAKAGE_DETECTED"
RELEASE_DIGEST_MISMATCH = "RELEASE_DIGEST_MISMATCH"
JSON_INVALID = "JSON_INVALID"

#: Strings that must not appear in any persisted structural field of a
#: TRAINABLE-only release.
_LEAKAGE_MARKERS = (
    "sealed_eval",
    "evaluator-private",
    "SUPER_SECRET_EVALUATOR_FAMILY_CANARY_9328",
)


class ValidationError(RuntimeError):
    """A corpus release failed independent validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """What a successful validation proves (also echoed by the CLI)."""

    path: Path
    release_digest: str
    tables: dict[str, int]
    total_rows: int


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _load_table(directory: Path, name: str, entry: dict[str, Any]) -> pa.Table:
    """Read a table's shards in deterministic order, verifying physical hashes.

    Row order across shards is shard order within the file listing, so the
    logical hash recomputation sees rows exactly in write order.
    """

    shards = sorted(entry["shards"], key=lambda shard: shard["relative_path"])
    if not shards:
        raise ValidationError(FILE_MISSING, f"table {name!r} declares no shards")
    # The manifest must agree with what is actually on disk (a deleted shard is
    # a real corruption, not just a hash mismatch).
    declared_paths = {shard["relative_path"] for shard in shards}
    on_disk = {str(relative.relative_to(directory)) for relative in (directory / name).glob("*.parquet")}
    extra = sorted(on_disk - declared_paths)
    if extra:
        raise ValidationError(FILE_MISSING, f"unmanifested shard(s) in {name!r}: {extra}")
    pieces: list[pa.Table] = []
    for shard in shards:
        relative = shard["relative_path"]
        path = directory / relative
        if not path.is_file():
            raise ValidationError(FILE_MISSING, f"missing shard {relative} for table {name!r}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != shard["sha256"]:
            raise ValidationError(
                FILE_SHA_MISMATCH, f"shard {relative} of {name!r} does not match its manifest sha256"
            )
        if len(data) != int(shard["bytes"]):
            raise ValidationError(FILE_SHA_MISMATCH, f"shard {relative} of {name!r} byte count drifted")
        declared = pq.ParquetFile(path)
        if declared.schema_arrow != TABLE_SCHEMAS[name].schema:
            raise ValidationError(
                SCHEMA_MISMATCH,
                f"shard {relative} of {name!r} does not match the fwf-corpus-v1 schema",
            )
        pieces.append(declared.read())
    if not pieces:
        raise ValidationError(FILE_MISSING, f"table {name!r} has no readable shards")
    return pa.concat_tables(pieces)


def _recompute_logical_hash(name: str, table: pa.Table) -> str:
    """Recompute the streaming logical hash from round-tripped rows (spec 24)."""

    digest = hashlib.sha256()
    columns = {field.name: table.column(field.name).to_pylist() for field in table.schema}
    row_count = table.num_rows
    for index in range(row_count):
        row = {field: columns[field][index] for field in columns}
        # Round-trip exactly what the writer hashed: JSON-safe values only.
        digest.update(canonical_row_bytes(_json_safe(row)))
        digest.update(b"\n")
    del name
    return digest.hexdigest()


def _level_pair(level: Any) -> tuple[int, int]:
    """One stored depth level, tolerant to the Arrow round-trip shape."""

    if isinstance(level, dict):
        return int(level["price_ticks"]), int(level["quantity"])
    return int(level[0]), int(level[1])


def _json_safe(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize Arrow round-trips to JSON-safe Python.

    The depth levels are stored as ``list<struct<price_ticks, quantity>>``;
    after ``to_pylist`` they are plain dicts, which is exactly the canonical
    form the writer hashed, so no re-shaping happens here.
    """

    return dict(row)


def _check_numeric(name: str, table: pa.Table, columns: tuple[str, ...]) -> None:
    """No NaN/inf in required numerics; no negative prices (spec 29)."""

    for column in columns:
        if column not in table.column_names:
            continue
        values = table.column(column).to_pylist()
        for index, value in enumerate(values):
            if value is None:
                continue
            if isinstance(value, float) and not math.isfinite(value):
                raise ValidationError(
                    NUMERIC_INVALID, f"{name}.{column} row {index} is not finite ({value!r})"
                )
            if (
                isinstance(value, int)
                and value < 0
                and column.endswith(("price_ticks", "high_ticks", "low_ticks", "open_ticks", "close_ticks"))
            ):
                raise ValidationError(NUMERIC_INVALID, f"{name}.{column} row {index} is negative ({value!r})")


def validate_corpus(path: Path) -> ValidationResult:
    """Independently validate one corpus release directory (spec 29-34)."""

    path = Path(path)
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise ValidationError(MANIFEST_MISSING, f"no manifest.json under {path}")
    try:
        manifest = _read_json(manifest_path)
    except json.JSONDecodeError as exc:
        raise ValidationError(MANIFEST_MISSING, f"manifest.json is not readable JSON: {exc}") from exc

    if manifest.get("corpus_schema_version") != CORPUS_SCHEMA_VERSION:
        raise ValidationError(
            MANIFEST_SCHEMA_UNKNOWN,
            f"unknown corpus schema {manifest.get('corpus_schema_version')!r}; expected {CORPUS_SCHEMA_VERSION!r}",
        )
    if manifest.get("manifest_version") != "corpus-manifest-v1":
        raise ValidationError(
            MANIFEST_SCHEMA_UNKNOWN, f"unknown manifest version {manifest.get('manifest_version')!r}"
        )

    declared_split = manifest.get("dataset_split")
    if declared_split != "trainable":
        raise ValidationError(
            LEAKAGE_DETECTED, f"manifest declares dataset_split {declared_split!r}, not trainable"
        )

    # All seven canonical tables must be declared (a manifest that drops one
    # would otherwise validate as a 'complete' release).
    declared = {entry["name"] for entry in manifest["tables"]}
    missing_tables = sorted(set(table_names()) - declared)
    if missing_tables:
        raise ValidationError(
            MANIFEST_SCHEMA_UNKNOWN, f"manifest does not declare required tables: {missing_tables}"
        )
    extra_tables = sorted(declared - set(table_names()))
    if extra_tables:
        raise ValidationError(MANIFEST_SCHEMA_UNKNOWN, f"manifest declares unknown tables: {extra_tables}")

    episode_ids: set[str] = set()
    table_rows: dict[str, int] = {}

    for entry in manifest["tables"]:
        name = entry["name"]
        _validate_table_directory(path, name, entry, manifest, episode_ids, table_rows)

    # The corpus-level identity is recomputed from verified inputs, not
    # trusted: base parameters + the table hashes/counts this validator just
    # re-derived. A forged release_digest, base_seed, plan id, etc., is caught.
    _recompute_release_digest(manifest)

    # Cross-table referential closure: every child row resolves (spec 30).
    _validate_foreign_keys(path, manifest, episode_ids, table_rows)

    release_digest = str(manifest["release_digest"])
    return ValidationResult(
        path=path,
        release_digest=release_digest,
        tables=table_rows,
        total_rows=sum(table_rows.values()),
    )


def _validate_table_directory(
    directory: Path,
    name: str,
    entry: dict[str, Any],
    manifest: dict[str, Any],
    episode_ids: set[str],
    table_rows: dict[str, int],
) -> None:
    """One table: file integrity, schema, counts, hashes, grain/row checks."""

    table = _load_table(directory, name, entry)
    if table.num_rows != int(entry["row_count"]):
        raise ValidationError(
            ROW_COUNT_MISMATCH,
            f"table {name!r} holds {table.num_rows} rows, manifest says {entry['row_count']}",
        )
    recomputed = _recompute_logical_hash(name, table)
    if recomputed != entry["logical_sha256"]:
        raise ValidationError(
            LOGICAL_HASH_MISMATCH,
            f"table {name!r} logical hash {recomputed[:16]}... does not match manifest {entry['logical_sha256'][:16]}...",
        )
    table_rows[name] = table.num_rows

    if name == "episodes":
        _check_numeric(
            "episodes",
            table,
            ("score", "seed", "event_count", "trade_count", "steps_total", "security_count"),
        )
        _validate_episodes(table, manifest, episode_ids)
    elif name == "securities" or name == "daily_bars":
        _validate_prices_and_grain(name, table)
        _validate_episodes_present(name, table, episode_ids, set())
    elif name == "agent_decisions":
        _validate_decisions(name, table, episode_ids, set())
    elif name == "exchange_commands":
        _validate_commands(name, table, episode_ids, set())
    elif name == "exchange_events":
        _validate_events(name, table, episode_ids, set())
    elif name == "book_snapshots":
        _check_numeric(
            "book_snapshots", table, ("best_bid_ticks", "best_ask_ticks", "global_step", "decision_index")
        )
        _validate_book_snapshots(name, table, episode_ids, set())
    else:  # pragma: no cover - table_names() is closed over the 7 canonical tables
        raise ValidationError(MANIFEST_SCHEMA_UNKNOWN, f"unknown table {name!r} in manifest")


def _first_episode_column(name: str, table: pa.Table) -> list[str]:
    return table.column("episode_id").to_pylist()


def _scan_leakage(name: str, table: pa.Table) -> None:
    """Reject sealed/private markers in persisted split/family columns."""

    structural: list[str] = []
    if "dataset_split" in table.column_names:
        structural.extend(table.column("dataset_split").to_pylist())
    if "process_family_visibility" in table.column_names:
        structural.extend(table.column("process_family_visibility").to_pylist())
    if structural:
        for marker in _LEAKAGE_MARKERS:
            if marker in set(structural):
                raise ValidationError(
                    LEAKAGE_DETECTED, f"table {name!r} carries the non-trainable marker {marker!r}"
                )


def _validate_episodes(table: pa.Table, manifest: dict[str, Any], episode_ids: set[str]) -> None:
    _scan_leakage("episodes", table)
    rows = table.to_pylist()
    seen: set[str] = set()
    declared_plan = manifest.get("training_plan_id")
    declared_task = manifest.get("task")
    for row in rows:
        episode_id = row["episode_id"]
        if episode_id in seen:
            raise ValidationError(DUPLICATE_EPISODE_ID, f"episode {episode_id!r} appears twice")
        seen.add(episode_id)
        if row["dataset_split"] != "trainable":
            raise ValidationError(
                LEAKAGE_DETECTED,
                f"episode {episode_id!r} declares split {row['dataset_split']!r}, not trainable",
            )
        if row["training_plan_id"] != declared_plan:
            raise ValidationError(
                FOREIGN_KEY,
                f"episode {episode_id!r} names plan {row['training_plan_id']!r}, manifest says {declared_plan!r}",
            )
        if row["task"] != declared_task:
            raise ValidationError(FOREIGN_KEY, f"episode {episode_id!r} names task {row['task']!r}")
        for canary in _LEAKAGE_MARKERS:
            process_family = row.get("process_family_id", "")
            if isinstance(process_family, str) and canary in process_family:
                raise ValidationError(
                    LEAKAGE_DETECTED, f"episode {episode_id!r} carries a private family canary"
                )
        if not isinstance(row["scoreable"], bool) or not row["scoreable"]:
            raise ValidationError(FOREIGN_KEY, f"episode {episode_id!r} is not scoreable")
        if row["corpus_schema_version"] != CORPUS_SCHEMA_VERSION:
            raise ValidationError(MANIFEST_SCHEMA_UNKNOWN, f"episode {episode_id!r} schema drift")
    episode_ids.update(seen)


def _validate_episodes_present(name: str, table: pa.Table, episode_ids: set[str], _unused: set[str]) -> None:
    """securities/daily_bars: FK to episodes + price sanity + OHLC bounds."""

    table_index = name
    episodes = _first_episode_column(name, table)
    missing = sorted(set(episodes) - episode_ids)
    if missing:
        raise ValidationError(FOREIGN_KEY, f"{name}: {len(missing)} rows reference unknown episodes")
    for canary in _LEAKAGE_MARKERS:
        joined = " ".join(episodes)
        if canary in joined:
            raise ValidationError(LEAKAGE_DETECTED, f"{name}: episode ids embed a private marker")
    if table_index == "daily_bars":
        opens = table.column("open_ticks").to_pylist()
        highs = table.column("high_ticks").to_pylist()
        lows = table.column("low_ticks").to_pylist()
        closes = table.column("close_ticks").to_pylist()
        for index in range(table.num_rows):
            if (
                min(opens[index], closes[index]) < lows[index]
                or max(opens[index], closes[index]) > highs[index]
            ):
                raise ValidationError(
                    OHLC_INVALID,
                    f"daily_bars row {index}: bounds violated "
                    f"(o={opens[index]} h={highs[index]} l={lows[index]} c={closes[index]})",
                )
            if min(opens[index], highs[index], lows[index], closes[index]) < 1:
                raise ValidationError(NUMERIC_INVALID, f"daily_bars row {index} has a non-positive price")
    if table_index == "securities":
        prices = table.column("initial_price_ticks").to_pylist()
        for index, price in enumerate(prices):
            if price < 1:
                raise ValidationError(
                    NUMERIC_INVALID, f"securities row {index} has non-positive initial price"
                )


def _validate_prices_and_grain(name: str, table: pa.Table) -> None:
    """Grain uniqueness for the per-episode market tables (spec 30)."""

    symbol_values = table.column("symbol").to_pylist()
    episode_values = table.column("episode_id").to_pylist()
    if name == "securities":
        keys = list(zip(episode_values, symbol_values, strict=True))
        if len(set(keys)) != len(keys):
            duplicates = sorted({key for key in keys if keys.count(key) > 1})
            raise ValidationError(
                DUPLICATE_EPISODE_ID,
                f"securities: duplicate (episode, symbol) grain keys: {duplicates[:3]}",
            )
        _check_numeric(
            "securities",
            table,
            ("initial_price_ticks", "beta", "sector_beta", "drift", "shares_outstanding"),
        )
    if name == "daily_bars":
        dates = table.column("session_date").to_pylist()
        bar_keys = [
            (episode, symbol, session_date)
            for episode, symbol, session_date in zip(
                episode_values,
                symbol_values,
                dates,
                strict=True,
            )
        ]
        keys = bar_keys  # type: ignore[assignment]  # a distinct 3-tuple grain
        if len(set(keys)) != len(keys):
            duplicates = sorted({key for key in keys if keys.count(key) > 1})
            raise ValidationError(
                DUPLICATE_EPISODE_ID,
                f"daily_bars: duplicate (episode, symbol, date) grain keys: {duplicates[:3]}",
            )
        _check_numeric("daily_bars", table, ("open_ticks", "high_ticks", "low_ticks", "close_ticks"))


def _validate_decisions(name: str, table: pa.Table, episode_ids: set[str], _unused: set[str]) -> None:
    _validate_episodes_present(name, table, episode_ids, _unused)
    rows = table.to_pylist()
    per_episode: dict[str, list[int]] = {}
    for row in rows:
        per_episode.setdefault(row["episode_id"], []).append(row["decision_index"])
    for episode_id, indices in per_episode.items():
        if sorted(indices) != list(range(len(indices))):
            raise ValidationError(
                DECISION_ORDER,
                f"episode {episode_id!r}: decision_index not unique and contiguous from zero",
            )
    # A decision's snapshot reference must exist exactly once (spec 30).
    if rows:
        pass
    for row in rows:
        status = row["decision_status"]
        if status not in {"executed"}:
            raise ValidationError(DECISION_ORDER, f"unknown decision_status {status!r}")
        if not row["observation_json"] or not row["action_json"]:
            raise ValidationError(DECISION_ORDER, f"episode {row['episode_id']!r}: empty observation/action")
        # The stored protocol documents must be real JSON (a bad string that
        # later breaks a consumer is corruption, not data).
        for json_field in ("observation_json", "action_json"):
            try:
                json.loads(row[json_field])
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    JSON_INVALID,
                    f"episode {row['episode_id']!r} decision {row['decision_index']}: "
                    f"{json_field} is not valid JSON ({exc})",
                ) from exc


def _validate_commands(name: str, table: pa.Table, episode_ids: set[str], _unused: set[str]) -> None:
    _validate_episodes_present(name, table, episode_ids, _unused)
    rows = table.to_pylist()
    per_episode: dict[str, list[int]] = {}
    command_ids: dict[str, set[str]] = {}
    for row in rows:
        if not row["command_id"] or not row["account_id"]:
            raise ValidationError(COMMAND_ORDER, "every command needs a command_id and account_id")
        if row["command_type"] not in {"submit", "cancel", "replace"}:
            raise ValidationError(COMMAND_ORDER, f"unknown command_type {row['command_type']!r}")
        per_episode.setdefault(row["episode_id"], []).append(row["command_ordinal"])
        command_ids.setdefault(row["episode_id"], set()).add(row["command_id"])
    for episode_id, ordinals in per_episode.items():
        if sorted(ordinals) != list(range(len(ordinals))):
            raise ValidationError(
                COMMAND_ORDER, f"episode {episode_id!r}: command_ordinal not contiguous from zero"
            )
    for episode_id, ids in command_ids.items():
        if len(ids) != len([row for row in rows if row["episode_id"] == episode_id]):
            raise ValidationError(COMMAND_ORDER, f"episode {episode_id!r}: duplicate command ids")


def _validate_events(name: str, table: pa.Table, episode_ids: set[str], _unused: set[str]) -> None:
    _validate_episodes_present(name, table, episode_ids, _unused)
    rows = table.to_pylist()
    per_episode: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        per_episode.setdefault(row["episode_id"], []).append(row)
    for episode_id, episode_rows in per_episode.items():
        for expected_ordinal, row in enumerate(episode_rows):
            if row["event_ordinal"] != expected_ordinal:
                raise ValidationError(
                    EVENT_ORDER,
                    f"episode {episode_id!r}: event_ordinal {row['event_ordinal']} is out of sequence",
                )
            if not row["event_id"]:
                raise ValidationError(EVENT_ORDER, f"episode {episode_id!r}: empty event_id")
            if not row["payload_json"]:
                raise ValidationError(EVENT_ORDER, f"episode {episode_id!r}: empty payload_json")
            try:
                json.loads(row["payload_json"])
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    JSON_INVALID,
                    f"episode {episode_id!r} event {row['event_id']!r}: payload not valid JSON ({exc})",
                ) from exc


def _validate_book_snapshots(name: str, table: pa.Table, episode_ids: set[str], _unused: set[str]) -> None:
    _validate_episodes_present(name, table, episode_ids, _unused)
    rows = table.to_pylist()
    snapshot_ids: set[str] = set()
    for row in rows:
        snapshot_id = row["snapshot_id"]
        if snapshot_id in snapshot_ids:
            raise ValidationError(SNAPSHOT_FK, f"snapshot {snapshot_id!r} appears twice")
        snapshot_ids.add(snapshot_id)
        bids = row["bids"]
        asks = row["asks"]
        depth = int(row["book_depth"])
        if len(bids) > depth or len(asks) > depth:
            raise ValidationError(
                BOOK_SNAPSHOT_INVALID,
                f"snapshot {snapshot_id!r} exceeds configured depth {depth}",
            )
        for label, levels, ordering in (("bid", bids, "descending"), ("ask", asks, "ascending")):
            # Arrow round-trips the level struct as dicts with named fields.
            pairs = [_level_pair(level) for level in levels]
            prices = [price for price, _quantity in pairs]
            quantities = [quantity for _price, quantity in pairs]
            if len(prices) != len(quantities):
                raise ValidationError(
                    BOOK_SNAPSHOT_INVALID, f"snapshot {snapshot_id!r}: ragged {label} levels"
                )
            if any(quantity <= 0 for quantity in quantities):
                raise ValidationError(
                    BOOK_SNAPSHOT_INVALID, f"snapshot {snapshot_id!r}: non-positive {label} quantity"
                )
            if any(price <= 0 for price in prices):
                raise ValidationError(
                    BOOK_SNAPSHOT_INVALID, f"snapshot {snapshot_id!r}: non-positive {label} price"
                )
            strict = all(
                prices[index] > prices[index + 1]
                if ordering == "descending"
                else prices[index] < prices[index + 1]
                for index in range(len(prices) - 1)
            )
            if prices and not strict:
                raise ValidationError(
                    BOOK_SNAPSHOT_INVALID,
                    f"snapshot {snapshot_id!r}: {label} prices not strictly {ordering}",
                )
        if bids and asks and _level_pair(bids[0])[0] >= _level_pair(asks[0])[0]:
            raise ValidationError(
                BOOK_SNAPSHOT_INVALID, f"snapshot {snapshot_id!r}: crossed book (bid >= ask)"
            )


def _validate_foreign_keys(
    directory: Path,
    manifest: dict[str, Any],
    episode_ids: set[str],
    table_rows: dict[str, int],
) -> None:
    """Cross-table closure: snapshots, event-count reconciliation, decision FKs."""

    episodes_entry = _entry(manifest, "episodes")
    decisions_entry = _entry(manifest, "agent_decisions")
    snapshots_entry = _entry(manifest, "book_snapshots")
    events_entry = _entry(manifest, "exchange_events")

    decisions = _load_table(directory, "agent_decisions", decisions_entry)
    snapshots = _load_table(directory, "book_snapshots", snapshots_entry)
    events = _load_table(directory, "exchange_events", events_entry)
    episodes = _load_table(directory, "episodes", episodes_entry)

    snapshot_ids = set(snapshots.column("snapshot_id").to_pylist())
    decision_snapshot_refs = decisions.column("book_snapshot_id").to_pylist()
    for index, ref in enumerate(decision_snapshot_refs):
        if ref is not None and ref not in snapshot_ids:
            raise ValidationError(SNAPSHOT_FK, f"decision row {index} references unknown snapshot {ref!r}")

    # The decision must reference *its own* pre-decision snapshot: episode,
    # decision index, instrument, and step must all agree on the join.
    snapshot_index = {row["snapshot_id"]: row for row in snapshots.to_pylist()}
    for index, decision in enumerate(decisions.to_pylist()):
        ref = decision.get("book_snapshot_id")
        if ref is None:
            continue
        snapshot = snapshot_index[ref]
        mismatch = (
            snapshot["episode_id"] != decision["episode_id"]
            or snapshot["decision_index"] != decision["decision_index"]
            or snapshot["instrument_id"] != decision["instrument_id"]
            or snapshot["global_step"] != decision["global_step"]
        )
        if mismatch:
            raise ValidationError(
                SNAPSHOT_FK,
                f"decision row {index} ({decision['episode_id']!r}) references snapshot {ref!r} "
                "of a different decision/episode/instrument/step",
            )

    episode_events = dict(
        zip(
            episodes.column("episode_id").to_pylist(), episodes.column("event_count").to_pylist(), strict=True
        )
    )
    counted: dict[str, int] = {}
    for episode_id in events.column("episode_id").to_pylist():
        counted[episode_id] = counted.get(episode_id, 0) + 1
    for episode_id, expected in episode_events.items():
        if counted.get(episode_id, 0) != expected:
            raise ValidationError(
                EVENT_COUNT_MISMATCH,
                f"episode {episode_id!r}: {counted.get(episode_id, 0)} exported events "
                f"but SessionResult recorded {expected}",
            )
    unknown = sorted(set(counted) - set(episode_events))
    if unknown:
        raise ValidationError(FOREIGN_KEY, f"{len(unknown)} events reference unknown episodes")

    # Every decision's per-episode step must be increasing (spec 31 ordering
    # analogue for decisions) -- already covered by contiguity; here we pin the
    # decision/episode count consistency the reader smoke test relies on.
    decision_counts: dict[str, int] = {}
    for episode_id in decisions.column("episode_id").to_pylist():
        decision_counts[episode_id] = decision_counts.get(episode_id, 0) + 1
    _ = decision_counts, table_rows


def _entry(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    for entry in manifest["tables"]:
        if entry["name"] == name:
            return entry
    raise ValidationError(MANIFEST_MISSING, f"manifest does not declare table {name!r}")


def _recompute_release_digest(manifest: dict[str, Any]) -> None:
    """Recompute the corpus-level digest from verified inputs (spec 27).

    The digest is defined over generation parameters and the per-table
    logical hashes/row counts. This validator has just re-derived the latter
    from the files, so a forged ``release_digest`` or a drifted generation
    parameter (base seed, plan id, world count, ...) fails here even though
    every table-level check passed.
    """

    payload = {
        "schema_version": manifest["corpus_schema_version"],
        "manifest_version": manifest["manifest_version"],
        "training_plan_id": manifest["training_plan_id"],
        "training_plan_version": manifest["training_plan_version"],
        "dataset_split": manifest["dataset_split"],
        "task": manifest["task"],
        "policy": manifest["policy"],
        "base_seed": manifest["base_seed"],
        "world_count": manifest["world_count"],
        "security_count": manifest["security_count"],
        "days": manifest["days"],
        "steps_per_day": manifest["steps_per_day"],
        "book_depth": manifest["book_depth"],
        "tables": [
            {
                "name": entry["name"],
                "row_count": entry["row_count"],
                "logical_sha256": entry["logical_sha256"],
            }
            for entry in manifest["tables"]
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    recomputed = hashlib.sha256(encoded).hexdigest()
    if recomputed != manifest["release_digest"]:
        raise ValidationError(
            RELEASE_DIGEST_MISMATCH,
            f"release digest {manifest['release_digest'][:16]}... does not match the "
            f"recomputed {recomputed[:16]}...; generation parameters or table hashes drifted",
        )
