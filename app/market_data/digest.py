"""Canonical dataset digest (Phase 3).

SHA-256 over all semantic content. Deterministic, order-independent for
instrument columns, and insensitive to irrelevant display metadata.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np


def compute_dataset_digest(panel: Any) -> str:
    """Compute the canonical SHA-256 dataset digest.

    Covers: schema version, timestamps, instrument stable IDs + ordering,
    OHLCV values, benchmark, eligibility mask, observed mask, imputation mask,
    adjustment policy, calendar policy, missing-data policy, provider identity
    + version, as_of timestamp, and source metadata affecting interpretation.

    Does NOT depend on: Python object identity, dict insertion order,
    machine-specific paths, cache location, or irrelevant display labels.

    Instrument-order invariant: columns are canonicalized by sorted
    ``stable_id`` before hashing, so reordering the instruments (and their
    associated OHLCV/mask columns) leaves the digest unchanged. Two panels that
    differ only by instrument permutation hash identically.
    """
    h = hashlib.sha256()

    # Schema version
    h.update(b"market-data-panel/v3.0")

    # Timestamps
    for d in panel.dates:
        h.update(d.isoformat().encode())

    # Canonical instrument permutation: sort by stable_id so the digest is
    # independent of the panel's column order.
    insts = list(panel.instruments)
    order = sorted(range(len(insts)), key=lambda i: insts[i].stable_id)

    # Instrument stable IDs in canonical order
    for i in order:
        h.update(insts[i].stable_id.encode())

    # OHLCV (round to 8 decimal places for float determinism), columns permuted
    # to the canonical order so a column reorder preserves the digest.
    for name in ("open", "high", "low", "close", "volume"):
        arr = getattr(panel, name)
        h.update(np.ascontiguousarray(np.round(arr[:, order], 8)).tobytes())

    # Benchmark
    if panel.benchmark_close is not None:
        h.update(np.ascontiguousarray(np.round(panel.benchmark_close, 8)).tobytes())
        h.update(panel.benchmark_instrument.stable_id.encode())

    # Masks (columns permuted to canonical order)
    h.update(np.ascontiguousarray(panel.eligibility_mask[:, order]).tobytes())
    h.update(np.ascontiguousarray(panel.observed_mask[:, order]).tobytes())
    h.update(np.ascontiguousarray(panel.imputation_mask[:, order]).tobytes())

    # Policies
    h.update(panel.adjustment_policy.value.encode())
    h.update(panel.calendar_policy.value.encode())
    h.update(panel.missing_data_policy.encode())
    h.update(panel.eligibility_source.value.encode())

    # Provider identity + version
    h.update(panel.provider.encode())
    h.update(panel.provider_version.encode())

    # as_of timestamp
    if panel.as_of is not None:
        h.update(panel.as_of.isoformat().encode())

    # Source metadata affecting interpretation (sorted, exclude display labels)
    semantic_meta = {
        k: v
        for k, v in panel.source_metadata.items()
        if k not in ("label", "display_name", "description", "notes")
    }
    h.update(json.dumps(semantic_meta, sort_keys=True, separators=(",", ":")).encode())

    return h.hexdigest()


__all__ = ["compute_dataset_digest"]
