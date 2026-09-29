"""Hidden-tier export of the causal graph (Milestone 7 support for CAUSAL-* QC rules).

The document carries the full validated registry, its hash, and, for every recorded
intervention, the set of variables the registry says the clamp can reach. An independent
checker can recompute that reach from the graph alone and compare.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from typing import Any

from app.causal.registry import MechanismRegistry, split_input

GRAPH_SCHEMA = "fwf-causal-graph/v1"


def reachable_variables(registry: MechanismRegistry, targets: Iterable[str]) -> list[str]:
    """Variable-level descendants (inclusive) following same-period and lagged edges."""

    seen: set[str] = set()
    queue: deque[str] = deque()
    for name in targets:
        if name not in seen:
            seen.add(name)
            queue.append(name)
    while queue:
        name = queue.popleft()
        for spec in registry.consumers(name):
            for out in spec.outputs:
                if out not in seen:
                    seen.add(out)
                    queue.append(out)
    return sorted(seen)


def causal_graph_document(
    registry: MechanismRegistry,
    interventions: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    records: list[dict[str, Any]] = [dict(iv) for iv in interventions]
    for record in records:
        name, _lagged = split_input(str(record["variable"]))
        record["variable"] = name
    return {
        "schema": GRAPH_SCHEMA,
        "registry_hash": registry.registry_hash(),
        "registry": registry.to_canonical(),
        "interventions": records,
        "intervention_reach": [reachable_variables(registry, [r["variable"]]) for r in records],
    }
