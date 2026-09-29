"""Canonical per-(variable, entity) serialization of a World V2 outcome."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from typing import Any

from app.causal import world_v2 as decl
from app.economy.v2 import WorldOutcomeV2

Key = tuple[str, str]  # (variable, entity)


def _dump(rows: list[tuple[Any, Any]]) -> bytes:
    return json.dumps(rows, default=str, separators=(",", ":"), allow_nan=True).encode()


def _entity(company: str) -> str:
    return f"COMPANY:{company}"


# table -> (attribute, row key column, declared value fields, time column for pre-start checks)
ROW_TABLES: dict[str, tuple[str, str, tuple[str, ...], str | None]] = {
    "quarters": ("quarters", "period_end", (*decl.QUARTER_FIELDS, "fraud_flag"), "period_end"),
    "balance_sheets": ("balance_sheets", "period_end", decl.BALANCE_FIELDS, "period_end"),
    "cash_flows": ("cash_flows", "period_end", decl.CASH_FLOW_FIELDS, "period_end"),
    "filings": ("filings", "period_end", decl.FILING_FIELDS, "period_end"),
    "earnings": ("earnings", "period_end", decl.EARNINGS_FIELDS, "period_end"),
    "estimates": ("estimates", "period", decl.ESTIMATE_FIELDS, None),
    "revisions": ("revisions", "period", decl.REVISION_FIELDS, None),
    "prices": ("prices", "session", decl.PRICE_FIELDS, "session"),
}


def flatten(outcome: WorldOutcomeV2, *, before: date | None = None) -> dict[Key, bytes]:
    """Serialize every observable variable per entity.

    Rows of a variable are ordered as the engine emitted them and carry their key, so a
    missing or reordered row changes the bytes. ``before`` keeps only rows whose time
    column precedes the date (tables without a time column are dropped).
    """

    tickers = [c["ticker"] for c in outcome.companies]
    out: dict[Key, bytes] = {}

    def keep(time_value: Any) -> bool:
        return before is None or (time_value is not None and time_value < before)

    for table, (attr, key_col, fields, time_col) in ROW_TABLES.items():
        if before is not None and time_col is None:
            continue
        rows = [asdict(r) for r in getattr(outcome, attr)]
        for name in fields:
            for ticker in tickers:
                series = [
                    (r[key_col], r[name])
                    for r in rows
                    if r["company"] == ticker and (time_col is None or keep(r[time_col]))
                ]
                out[(f"{table}.{name}", _entity(ticker))] = _dump(series)

    # macro (world scope)
    for name in (*decl.MACRO_REAL_FIELDS, *decl.MACRO_RATE_FIELDS):
        series = [(m.date, getattr(m, name)) for m in outcome.macro if keep(m.date)]
        out[(f"macro.{name}", decl.ENTITY_WORLD)] = _dump(series)

    # events, split by kind (single-writer variables)
    if before is None:
        for kind in ("earnings_call", "filing"):
            for ticker in tickers:
                series = [(e.at, e.payload) for e in outcome.events if e.company == ticker and e.kind == kind]
                out[(f"events.{kind}", _entity(ticker))] = _dump(series)

    # hidden latents
    for key in decl.LATENT_SOURCES:
        for ticker in tickers:
            series = [
                (snap.date, snap.values[key]) for snap in outcome.latents.get(ticker, []) if keep(snap.date)
            ]
            out[(f"latents.{key}", _entity(ticker))] = _dump(series)

    if before is None:
        for ticker in tickers:
            out[("defaults", _entity(ticker))] = _dump(
                [(d["at"], d["debt_stress"]) for d in outcome.defaults if d["company"] == ticker]
            )
            out[("fraud_windows", _entity(ticker))] = _dump(
                [(w["start"], w["end"]) for w in outcome.fraud_windows if w["company"] == ticker]
            )
    return out


def changed(base: dict[Key, bytes], twin: dict[Key, bytes]) -> set[Key]:
    assert base.keys() == twin.keys()
    return {k for k in base if base[k] != twin[k]}


def descendant_filter(nodes: frozenset[Key]) -> Callable[[Key], bool]:
    return nodes.__contains__
