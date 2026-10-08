"""Explicit ``fwf-corpus-v1`` Arrow schemas (M10.7).

Every release table is declared here as a literal :class:`pyarrow.Schema`; no
table is ever inferred from Python objects or Pandas frames, so a drifted row
cannot silently widen a column type behind the release digest. Each table also
carries a per-table ``schema_version`` that is stored in the manifest, so a
future ``fwf-corpus-v2`` can evolve one table without renaming the corpus.

Grains (one row per ...):

* ``episodes``          -- generated world/session episode
* ``securities``        -- synthetic security per episode
* ``daily_bars``        -- episode x security x synthetic trading day
* ``agent_decisions``   -- external/reference-agent decision (one observation
                           + one validated action at the decision boundary)
* ``exchange_commands`` -- canonical exchange command, recorded at creation time
* ``exchange_events``   -- ``OrderEventV2`` in immutable-ledger order
* ``book_snapshots``    -- aggregate L10 depth captured for one decision

The schemas are the *format* boundary for future PyTorch / JAX / HF adapters,
which is why observations and actions stay canonical JSON strings here: the
protocol documents themselves (``StrategyObservationV2`` /
``StrategyActionV2`` schema 2.0) are the typed contract, and the corpus stores
exactly what the agent saw and returned.
"""

from __future__ import annotations

from dataclasses import dataclass

import pyarrow as pa

__all__ = [
    "CORPUS_SCHEMA_VERSION",
    "TABLE_SCHEMAS",
    "CorpusTable",
    "table_names",
    "table_schema",
]

#: The canonical initial release schema. Stored on every table row and in the
#: manifest; the validator refuses a release that claims any other version.
CORPUS_SCHEMA_VERSION = "fwf-corpus-v1"

#: Schema version of the observation/action protocol documents stored in
#: ``agent_decisions`` (the benchmark's published agent protocol).
AGENT_PROTOCOL_SCHEMA_VERSION = "2.0"

_TICKS = pa.int64()
_QUANTITY = pa.int64()

_LEVELS = pa.list_(
    pa.struct(
        [pa.field("price_ticks", _TICKS, nullable=False), pa.field("quantity", _QUANTITY, nullable=False)]
    )
)


@dataclass(frozen=True, slots=True)
class CorpusTable:
    """One release table: identity, explicit schema, and declared grain."""

    name: str
    schema_version: str
    grain: str
    schema: pa.Schema


_EPISODES = pa.schema(
    [
        pa.field("corpus_schema_version", pa.string(), nullable=False),
        pa.field("episode_id", pa.string(), nullable=False),
        pa.field("world_id", pa.string(), nullable=False),
        pa.field("world_index", pa.int64(), nullable=False),
        pa.field("dataset_split", pa.string(), nullable=False),
        pa.field("training_plan_id", pa.string(), nullable=False),
        pa.field("training_plan_version", pa.string(), nullable=False),
        pa.field("task", pa.string(), nullable=False),
        pa.field("policy_id", pa.string(), nullable=False),
        pa.field("seed", pa.int64(), nullable=False),
        pa.field("universe_id", pa.string(), nullable=False),
        pa.field("process_family_id", pa.string(), nullable=False),
        pa.field("process_family_digest", pa.string(), nullable=False),
        pa.field("ecology_id", pa.string(), nullable=False),
        pa.field("session_count", pa.int64(), nullable=False),
        pa.field("steps_total", pa.int64(), nullable=False),
        pa.field("security_count", pa.int64(), nullable=False),
        pa.field("market_logical_sha256", pa.string(), nullable=False),
        pa.field("ledger_digest", pa.string(), nullable=False),
        pa.field("action_digest", pa.string(), nullable=False),
        pa.field("event_count", pa.int64(), nullable=False),
        pa.field("trade_count", pa.int64(), nullable=False),
        pa.field("order_count", pa.int64(), nullable=False),
        pa.field("cancel_count", pa.int64(), nullable=False),
        pa.field("replace_count", pa.int64(), nullable=False),
        pa.field("score", pa.float64(), nullable=False),
        pa.field("scoreable", pa.bool_(), nullable=False),
        pa.field("metrics_json", pa.string(), nullable=False),
        pa.field("generator_bundle_digest", pa.string(), nullable=False),
        pa.field("seed_material_digest", pa.string(), nullable=False),
        # Forward compatibility only: fwf-corpus-v1 defines terminal episode
        # scoring, NOT a dense per-step reward. Always null in v1.
        pa.field("reward", pa.float64(), nullable=True),
    ]
)

_SECURITIES = pa.schema(
    [
        pa.field("corpus_schema_version", pa.string(), nullable=False),
        pa.field("episode_id", pa.string(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("sector", pa.string(), nullable=False),
        pa.field("initial_price_ticks", _TICKS, nullable=False),
        pa.field("liquidity", pa.string(), nullable=False),
        pa.field("beta", pa.float64(), nullable=False),
        pa.field("sector_beta", pa.float64(), nullable=False),
        pa.field("drift", pa.float64(), nullable=False),
        pa.field("shares_outstanding", _QUANTITY, nullable=False),
    ]
)

_DAILY_BARS = pa.schema(
    [
        pa.field("corpus_schema_version", pa.string(), nullable=False),
        pa.field("episode_id", pa.string(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("session_date", pa.string(), nullable=False),
        pa.field("open_ticks", _TICKS, nullable=False),
        pa.field("high_ticks", _TICKS, nullable=False),
        pa.field("low_ticks", _TICKS, nullable=False),
        pa.field("close_ticks", _TICKS, nullable=False),
    ]
)

_AGENT_DECISIONS = pa.schema(
    [
        pa.field("corpus_schema_version", pa.string(), nullable=False),
        pa.field("episode_id", pa.string(), nullable=False),
        pa.field("decision_index", pa.int64(), nullable=False),
        pa.field("global_step", pa.int64(), nullable=False),
        pa.field("day_index", pa.int64(), nullable=False),
        pa.field("instrument_id", pa.string(), nullable=False),
        pa.field("observation_schema_version", pa.string(), nullable=False),
        pa.field("action_schema_version", pa.string(), nullable=False),
        pa.field("observation_json", pa.string(), nullable=False),
        pa.field("action_json", pa.string(), nullable=False),
        pa.field("action_type", pa.string(), nullable=False),
        pa.field("side", pa.string(), nullable=True),
        pa.field("order_type", pa.string(), nullable=True),
        pa.field("quantity", _QUANTITY, nullable=True),
        pa.field("limit_price_ticks", _TICKS, nullable=True),
        pa.field("order_id", pa.string(), nullable=True),
        pa.field("decision_status", pa.string(), nullable=False),
        pa.field("ledger_event_index_before", pa.int64(), nullable=False),
        pa.field("ledger_event_index_after", pa.int64(), nullable=False),
        pa.field("book_snapshot_id", pa.string(), nullable=True),
    ]
)

_EXCHANGE_COMMANDS = pa.schema(
    [
        pa.field("corpus_schema_version", pa.string(), nullable=False),
        pa.field("episode_id", pa.string(), nullable=False),
        pa.field("command_ordinal", pa.int64(), nullable=False),
        pa.field("command_id", pa.string(), nullable=False),
        pa.field("command_type", pa.string(), nullable=False),
        pa.field("account_id", pa.string(), nullable=False),
        pa.field("order_id", pa.string(), nullable=True),
        pa.field("instrument_id", pa.string(), nullable=True),
        pa.field("side", pa.string(), nullable=True),
        pa.field("order_type", pa.string(), nullable=True),
        pa.field("time_in_force", pa.string(), nullable=True),
        pa.field("quantity", _QUANTITY, nullable=True),
        pa.field("price_ticks", _TICKS, nullable=True),
        pa.field("exchange_time_ns", pa.int64(), nullable=False),
        pa.field("venue_sequence", pa.int64(), nullable=False),
    ]
)

_EXCHANGE_EVENTS = pa.schema(
    [
        pa.field("corpus_schema_version", pa.string(), nullable=False),
        pa.field("episode_id", pa.string(), nullable=False),
        pa.field("event_ordinal", pa.int64(), nullable=False),
        pa.field("event_id", pa.string(), nullable=False),
        pa.field("kind", pa.string(), nullable=False),
        pa.field("exchange_time_ns", pa.int64(), nullable=False),
        pa.field("venue_sequence", pa.int64(), nullable=False),
        pa.field("event_priority", pa.int64(), nullable=False),
        pa.field("command_id", pa.string(), nullable=False),
        pa.field("order_id", pa.string(), nullable=False),
        pa.field("payload_json", pa.string(), nullable=False),
    ]
)

_BOOK_SNAPSHOTS = pa.schema(
    [
        pa.field("corpus_schema_version", pa.string(), nullable=False),
        pa.field("snapshot_id", pa.string(), nullable=False),
        pa.field("episode_id", pa.string(), nullable=False),
        pa.field("decision_index", pa.int64(), nullable=False),
        pa.field("global_step", pa.int64(), nullable=False),
        pa.field("instrument_id", pa.string(), nullable=False),
        pa.field("book_depth", pa.int64(), nullable=False),
        # The touch is level 0 of the side; kept as scalar columns for cheap
        # analytics. Nullable: a one-sided (or empty) book has no touch.
        pa.field("best_bid_ticks", _TICKS, nullable=True),
        pa.field("best_ask_ticks", _TICKS, nullable=True),
        # Aggregate depth: ordered lists of (price, quantity) structs. Bids
        # arrive highest-price-first, asks lowest-price-first; quantities are
        # aggregate displayed sizes. No account or queue identity is stored.
        pa.field("bids", _LEVELS, nullable=False),
        pa.field("asks", _LEVELS, nullable=False),
    ]
)

#: The seven canonical release tables, in manifest order.
TABLE_SCHEMAS: dict[str, CorpusTable] = {
    "episodes": CorpusTable(
        name="episodes",
        schema_version="v1",
        grain="one row per generated episode",
        schema=_EPISODES,
    ),
    "securities": CorpusTable(
        name="securities",
        schema_version="v1",
        grain="one row per synthetic security per episode",
        schema=_SECURITIES,
    ),
    "daily_bars": CorpusTable(
        name="daily_bars",
        schema_version="v1",
        grain="one row per episode x security x synthetic trading day",
        schema=_DAILY_BARS,
    ),
    "agent_decisions": CorpusTable(
        name="agent_decisions",
        schema_version="v1",
        grain="one row per agent decision at the decision boundary",
        schema=_AGENT_DECISIONS,
    ),
    "exchange_commands": CorpusTable(
        name="exchange_commands",
        schema_version="v1",
        grain="one row per canonical exchange command, recorded at creation",
        schema=_EXCHANGE_COMMANDS,
    ),
    "exchange_events": CorpusTable(
        name="exchange_events",
        schema_version="v1",
        grain="one row per OrderEventV2, in immutable-ledger order",
        schema=_EXCHANGE_EVENTS,
    ),
    "book_snapshots": CorpusTable(
        name="book_snapshots",
        schema_version="v1",
        grain="one row per pre-decision aggregate L10 depth capture",
        schema=_BOOK_SNAPSHOTS,
    ),
}


def table_names() -> tuple[str, ...]:
    """The canonical table order (manifest order, creation order for writers)."""

    return tuple(TABLE_SCHEMAS)


def table_schema(name: str) -> pa.Schema:
    """The explicit Arrow schema of ``name``; a typo is an error, not an empty table."""

    try:
        return TABLE_SCHEMAS[name].schema
    except KeyError as exc:
        raise KeyError(f"unknown corpus table {name!r}") from exc
