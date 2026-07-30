"""Documented schema converters for StrategySpec.

Reset brief Phase 1.1 item 6 requires an explicit, documented converter from the
prior draft schema (``strategy-spec/v1``) to the current ``strategy-spec/v1.1``
rather than silently reinterpreting old fields.

Field changes v1 -> v1.1:

* ``strategy_id`` in v1 defaulted to the content hash; in v1.1 identity is a
  standalone UUID. Converting mints a fresh UUID (a v1 doc's "id" was never a
  stable logical identity, so preserving it would perpetuate the identity ==
  content-hash confusion the reset fixes).
* Weights/exposures move from float to Decimal-normalized values (handled by the
  v1.1 model's ``FinDecimal`` validators; no manual coercion needed).
* ``order_policy`` becomes the typed ``OrderPolicy`` model; a bare v1 dict is
  mapped by key.
* ``benchmark_tradable`` is new (defaults False -- the v1.1-safe default).
* Net/gross/position exposure live only under ``risk_constraints`` in v1.1; any
  v1 copies under ``portfolio_construction`` are dropped in favor of the
  ``risk_constraints`` values (documented precedence, no silent duplication).

This converter is intentionally strict: unknown top-level keys raise, because a
silent drop is exactly the class of bug the reset is eliminating.
"""

from __future__ import annotations

from typing import Any

from app.domain.strategy_spec import SCHEMA_VERSION, StrategySpec

_V1 = "strategy-spec/v1"

# Top-level keys that legitimately existed in v1 and are understood here.
_KNOWN_V1_KEYS = {
    "schema_version",
    "strategy_id",
    "strategy_version",
    "name",
    "original_thesis",
    "strategy_type",
    "universe",
    "benchmark",
    "frequency",
    "execution_timing",
    "order_policy",
    "portfolio_construction",
    "risk_constraints",
    "clauses",
    "unsupported_clauses",
    "compiler_metadata",
}

_EXPOSURE_KEYS = {
    "gross_exposure_limit",
    "net_exposure_target",
    "net_exposure_limit",
    "max_position_weight",
}


def convert_v1_to_v1_1(doc: dict[str, Any]) -> StrategySpec:
    """Convert a v1 spec dict to a validated v1.1 ``StrategySpec``.

    Raises ``ValueError`` on unknown keys (no silent reinterpretation).
    """
    schema = doc.get("schema_version", _V1)
    if schema not in (_V1, SCHEMA_VERSION):
        raise ValueError(f"cannot convert schema {schema!r}; expected {_V1!r}")
    if schema == SCHEMA_VERSION:
        # Already v1.1; validate as-is.
        return StrategySpec.model_validate(doc)

    unknown = set(doc) - _KNOWN_V1_KEYS
    if unknown:
        raise ValueError(f"unknown v1 keys, refusing to silently drop: {sorted(unknown)}")

    out: dict[str, Any] = {k: v for k, v in doc.items() if k != "strategy_id"}
    out["schema_version"] = SCHEMA_VERSION
    # Mint a fresh identity (v1 id was the content hash -> not a real identity).
    # strategy_id omitted => v1.1 default_factory assigns a UUID.

    # Move any exposure fields duplicated under portfolio_construction into
    # risk_constraints (single source of truth), dropping the pc copies.
    pc = dict(out.get("portfolio_construction") or {})
    rc = dict(out.get("risk_constraints") or {})
    for key in list(pc):
        if key in _EXPOSURE_KEYS:
            rc.setdefault(key, pc.pop(key))
    if pc:
        out["portfolio_construction"] = pc
    if rc:
        out["risk_constraints"] = rc

    # benchmark_tradable defaults False in v1.1 (safe: benchmark not tradable).
    out.setdefault("benchmark_tradable", False)

    return StrategySpec.model_validate(out)


__all__ = ["convert_v1_to_v1_1"]
