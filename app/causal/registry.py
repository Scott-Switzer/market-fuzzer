"""Versioned MechanismSpec registry and per-period causal DAG.

Contract (docs/fwf/06-causal-markets.md in financial-system-core):

* Every mechanism is a registered, versioned node with declared inputs, outputs,
  RNG address templates, frequency, and output tier.
* The registry builds a directed acyclic graph per period. A cycle among
  same-period reads is rejected. Cross-period feedback is legal only through a
  lagged input, written ``"<variable>@t-1"``.
* Every variable has exactly one producer (single writer) unless it is declared
  exogenous, in which case it has none.
* A mechanism may read variables of its own scope or a broader one
  (company < sector < world). A broader mechanism reading a narrower variable
  would create an implicit cross-entity channel and is rejected.
* Mechanisms never write statement fields directly; that rule is enforced by the
  ledger, not by this registry.

The registry is descriptive and deterministic: it has no RNG and reads no clock.
``descendants`` returns a conservative superset of the variables an intervention
can change, which is what the twin-world invariance test needs (everything
outside the set must be byte-identical).
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

Scope = Literal["world", "sector", "company"]
Frequency = Literal["quarter", "day"]
Tier = Literal["public", "evaluator", "hidden"]

SCOPE_RANK: Mapping[str, int] = {"company": 0, "sector": 1, "world": 2}
LAG_SUFFIX = "@t-1"
REGISTRY_SCHEMA_VERSION = "MechanismRegistryV1"


class CausalRegistryError(ValueError):
    """Base class for registry rejections."""


class RegistryStructureError(CausalRegistryError):
    """A declaration violates the single-writer, scope, or reference rules."""


class CausalCycleError(CausalRegistryError):
    """Same-period reads form a cycle. ``cycle`` lists mechanism ids, first == last."""

    def __init__(self, cycle: Sequence[str]) -> None:
        self.cycle = tuple(cycle)
        super().__init__("same-period causal cycle: " + " -> ".join(self.cycle))


@dataclass(frozen=True)
class VariableSpec:
    """A declared variable. Produced variables are declared by their mechanism."""

    name: str
    scope: Scope
    tier: Tier = "public"
    exogenous: bool = False
    intervenable: bool = False


@dataclass(frozen=True)
class MechanismSpec:
    id: str
    version: int
    scope: Scope
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    rng_addresses: tuple[str, ...] = ()
    frequency: Frequency = "quarter"
    tier_of_outputs: Tier = "public"
    doc: str = ""

    def __post_init__(self) -> None:
        if not self.id or self.version < 1:
            raise RegistryStructureError("mechanism needs a non-empty id and version >= 1")
        if self.scope not in SCOPE_RANK:
            raise RegistryStructureError(f"{self.id}: unknown scope {self.scope!r}")
        if not self.outputs:
            raise RegistryStructureError(f"{self.id}: a mechanism must declare at least one output")
        for tup_name in ("inputs", "outputs", "rng_addresses"):
            values = getattr(self, tup_name)
            if not isinstance(values, tuple):
                raise RegistryStructureError(f"{self.id}: {tup_name} must be a tuple")
            if len(set(values)) != len(values):
                raise RegistryStructureError(f"{self.id}: duplicate entries in {tup_name}")
        for name in self.outputs:
            if name.endswith(LAG_SUFFIX):
                raise RegistryStructureError(f"{self.id}: outputs cannot be lagged ({name})")


def split_input(reference: str) -> tuple[str, bool]:
    """Return ``(variable, lagged)`` for an input reference."""

    if reference.endswith(LAG_SUFFIX):
        return reference[: -len(LAG_SUFFIX)], True
    return reference, False


class MechanismRegistry:
    """Mutable during construction; call :meth:`validate` before use."""

    def __init__(self) -> None:
        self._mechanisms: dict[str, MechanismSpec] = {}
        self._exogenous: dict[str, VariableSpec] = {}
        self._clamps: frozenset[str] = frozenset()
        self._validated = False

    # ------------------------------------------------------------------ #
    # declaration
    # ------------------------------------------------------------------ #

    def declare_exogenous(
        self, name: str, scope: Scope, *, tier: Tier = "public", intervenable: bool = False
    ) -> None:
        if name in self._exogenous:
            raise RegistryStructureError(f"exogenous variable {name!r} declared twice")
        if name.endswith(LAG_SUFFIX):
            raise RegistryStructureError(f"variable names cannot carry {LAG_SUFFIX}: {name}")
        self._exogenous[name] = VariableSpec(
            name=name, scope=scope, tier=tier, exogenous=True, intervenable=intervenable
        )
        self._validated = False

    def register(self, spec: MechanismSpec) -> MechanismSpec:
        if spec.id in self._mechanisms:
            raise RegistryStructureError(f"mechanism {spec.id!r} registered twice")
        self._mechanisms[spec.id] = spec
        self._validated = False
        return spec

    # ------------------------------------------------------------------ #
    # views
    # ------------------------------------------------------------------ #

    @property
    def mechanisms(self) -> Mapping[str, MechanismSpec]:
        return dict(sorted(self._mechanisms.items()))

    def variables(self) -> dict[str, VariableSpec]:
        out: dict[str, VariableSpec] = dict(self._exogenous)
        for spec in self._mechanisms.values():
            for name in spec.outputs:
                if name in out:
                    raise RegistryStructureError(f"variable {name!r} has more than one writer")
                out[name] = VariableSpec(name=name, scope=spec.scope, tier=spec.tier_of_outputs)
        return dict(sorted(out.items()))

    def producer(self, variable: str) -> str | None:
        for spec in self._mechanisms.values():
            if variable in spec.outputs:
                return spec.id
        return None

    def intervenable_variables(self) -> frozenset[str]:
        """Variables an intervention may clamp: exogenous flagged ones plus any explicit list."""

        return frozenset(name for name, var in self._exogenous.items() if var.intervenable) | self._clamps

    def allow_intervention(self, *names: str) -> None:
        """Mark produced variables as clampable (the do-operator cuts their incoming edges)."""

        known = self.variables()
        for name in names:
            if name not in known:
                raise RegistryStructureError(f"cannot mark unknown variable {name!r} intervenable")
        self._clamps = self._clamps | frozenset(names)

    # ------------------------------------------------------------------ #
    # validation
    # ------------------------------------------------------------------ #

    def validate(self) -> MechanismRegistry:
        variables = self.variables()  # raises on duplicate writers
        for name in self._exogenous:
            if self.producer(name) is not None:
                raise RegistryStructureError(f"exogenous variable {name!r} also has a producer")
        for spec in self._mechanisms.values():
            for reference in spec.inputs:
                name, _lagged = split_input(reference)
                var = variables.get(name)
                if var is None:
                    raise RegistryStructureError(
                        f"{spec.id}: input {reference!r} has no producer and is not exogenous"
                    )
                if SCOPE_RANK[var.scope] < SCOPE_RANK[spec.scope]:
                    raise RegistryStructureError(
                        f"{spec.id} ({spec.scope}) reads narrower-scope variable {name!r} ({var.scope})"
                    )
        self.period_dag()  # raises CausalCycleError
        self._validated = True
        return self

    def _require_validated(self) -> None:
        if not self._validated:
            self.validate()

    # ------------------------------------------------------------------ #
    # graphs
    # ------------------------------------------------------------------ #

    def _same_period_edges(self) -> dict[str, set[str]]:
        """Mechanism -> set of mechanisms that read one of its outputs in the same period."""

        writers = {out: spec.id for spec in self._mechanisms.values() for out in spec.outputs}
        edges: dict[str, set[str]] = {mid: set() for mid in self._mechanisms}
        for spec in self._mechanisms.values():
            for reference in spec.inputs:
                name, lagged = split_input(reference)
                if lagged:
                    continue
                upstream = writers.get(name)
                if upstream is not None:
                    edges[upstream].add(spec.id)
        return edges

    def period_dag(self) -> tuple[str, ...]:
        """Deterministic topological order of mechanisms within one period.

        Raises :class:`CausalCycleError` with a concrete cycle when same-period reads loop.
        Ties are broken by mechanism id so the order is byte-stable.
        """

        edges = self._same_period_edges()
        indegree = {mid: 0 for mid in edges}
        for targets in edges.values():
            for target in targets:
                indegree[target] += 1
        ready = sorted(mid for mid, degree in indegree.items() if degree == 0)
        order: list[str] = []
        queue = deque(ready)
        while queue:
            mid = queue.popleft()
            order.append(mid)
            released = []
            for target in sorted(edges[mid]):
                indegree[target] -= 1
                if indegree[target] == 0:
                    released.append(target)
            for target in sorted(released):
                queue.append(target)
        if len(order) != len(edges):
            raise CausalCycleError(self._find_cycle(edges, {mid for mid, d in indegree.items() if d > 0}))
        return tuple(order)

    @staticmethod
    def _find_cycle(edges: Mapping[str, set[str]], candidates: set[str]) -> list[str]:
        """Return one concrete cycle inside ``candidates`` (nodes left after Kahn's algorithm)."""

        color: dict[str, int] = {}
        stack: list[str] = []

        def visit(node: str) -> list[str] | None:
            color[node] = 1
            stack.append(node)
            for target in sorted(edges[node] & candidates):
                if color.get(target, 0) == 1:
                    return [*stack[stack.index(target) :], target]
                if color.get(target, 0) == 0:
                    found = visit(target)
                    if found:
                        return found
            stack.pop()
            color[node] = 2
            return None

        for start in sorted(candidates):
            if color.get(start, 0) == 0:
                found = visit(start)
                if found:
                    return found
        raise AssertionError("Kahn residue without a cycle")  # pragma: no cover

    def consumers(self, variable: str) -> tuple[MechanismSpec, ...]:
        """Mechanisms that read ``variable`` in either the same or the previous period."""

        return tuple(
            spec
            for _mid, spec in sorted(self._mechanisms.items())
            if any(split_input(ref)[0] == variable for ref in spec.inputs)
        )

    # ------------------------------------------------------------------ #
    # descendants
    # ------------------------------------------------------------------ #

    def descendants(
        self,
        targets: Iterable[tuple[str, str]],
        *,
        sector_of_company: Mapping[str, str],
        sectors: Iterable[str] | None = None,
    ) -> frozenset[tuple[str, str]]:
        """Instance-level descendants (inclusive) of ``(variable, entity)`` targets.

        Entities are ``"WORLD"``, ``"SECTOR:<name>"`` and ``"COMPANY:<ticker>"``.
        Lagged edges are followed, so the result covers all later periods too.
        The set is a conservative superset: a listed node may still be unchanged
        in a particular run, but an unlisted node can never change.
        """

        self._require_validated()
        variables = self.variables()
        company_entities = sorted(sector_of_company)
        sector_entities = sorted(set(sectors) if sectors is not None else set(sector_of_company.values()))

        def entities_for(out_scope: str, in_scope: str, entity: str) -> list[str]:
            if out_scope == "world":
                return ["WORLD"]
            if out_scope == "sector":
                if in_scope == "sector":
                    return [entity]
                return [f"SECTOR:{s}" for s in sector_entities]  # world input
            # company-scope output
            if in_scope == "company":
                return [entity]
            if in_scope == "sector":
                sector = entity.removeprefix("SECTOR:")
                return [c for c in company_entities if sector_of_company[c] == sector]
            return list(company_entities)

        seen: set[tuple[str, str]] = set()
        queue: deque[tuple[str, str]] = deque()
        for variable, entity in targets:
            if variable not in variables:
                raise RegistryStructureError(f"unknown intervention target {variable!r}")
            node = (variable, entity)
            if node not in seen:
                seen.add(node)
                queue.append(node)
        while queue:
            variable, entity = queue.popleft()
            in_scope = variables[variable].scope
            for spec in self.consumers(variable):
                for out_entity in entities_for(spec.scope, in_scope, entity):
                    for out in spec.outputs:
                        node = (out, out_entity)
                        if node not in seen:
                            seen.add(node)
                            queue.append(node)
        return frozenset(seen)

    # ------------------------------------------------------------------ #
    # canonical export
    # ------------------------------------------------------------------ #

    def to_canonical(self) -> dict[str, object]:
        self._require_validated()
        return {
            "schema": REGISTRY_SCHEMA_VERSION,
            "exogenous": [
                {
                    "name": var.name,
                    "scope": var.scope,
                    "tier": var.tier,
                    "intervenable": var.intervenable or var.name in self._clamps,
                }
                for var in sorted(self._exogenous.values(), key=lambda v: v.name)
            ],
            "clampable": sorted(self._clamps),
            "mechanisms": [
                {
                    "id": spec.id,
                    "version": spec.version,
                    "scope": spec.scope,
                    "inputs": sorted(spec.inputs),
                    "outputs": sorted(spec.outputs),
                    "rng_addresses": sorted(spec.rng_addresses),
                    "frequency": spec.frequency,
                    "tier_of_outputs": spec.tier_of_outputs,
                    "doc": spec.doc,
                }
                for _mid, spec in sorted(self._mechanisms.items())
            ],
            "period_order": list(self.period_dag()),
        }

    def registry_hash(self) -> str:
        payload = json.dumps(self.to_canonical(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode()).hexdigest()
