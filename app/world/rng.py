"""SEMANTIC_RNG_V3 primitives for World V2.

The implementation mirrors the canonical ``fwf-kernel`` algorithm: a
meaning-based address is hashed into a Philox key, while period and draw
coordinates occupy the counter.  No mutable sequential generator state is
used by the World V2 engine.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import numpy as np

NAMESPACE_VERSION = "SEMANTIC_RNG_V3"
TRANSFORM_VERSION = "T1"
TRANSFORM_VERSIONS: Mapping[str, str] = MappingProxyType(
    {"uniform01": TRANSFORM_VERSION, "normal": TRANSFORM_VERSION}
)
_TWO_POW_M53 = 2.0**-53


@dataclass(frozen=True)
class StreamAddress:
    world_id: str
    entity_id: str
    mechanism_id: str
    variable: str
    distribution_id: str

    def canonical(self) -> bytes:
        payload = [
            NAMESPACE_VERSION,
            self.world_id,
            self.entity_id,
            self.mechanism_id,
            self.variable,
            self.distribution_id,
        ]
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode()

    def key(self) -> np.ndarray:
        digest = hashlib.sha256(self.canonical()).digest()
        return np.array(
            [int.from_bytes(digest[0:8], "little"), int.from_bytes(digest[8:16], "little")],
            dtype=np.uint64,
        )


def raw_uint64(addr: StreamAddress, period_ordinal: int, draw_index: int, n: int = 1) -> np.ndarray:
    """Return bit-exact Philox raw draws for one semantic address."""

    if period_ordinal < 0 or draw_index < 0 or n < 1:
        raise ValueError("period_ordinal and draw_index must be >= 0 and n >= 1")
    counter = np.array([0, period_ordinal, draw_index, 0], dtype=np.uint64)
    bit_generator = np.random.Philox(key=addr.key(), counter=counter)
    return bit_generator.random_raw(n)


def uniform01(addr: StreamAddress, period_ordinal: int, draw_index: int, n: int = 1) -> np.ndarray:
    """Return exact 53-bit uniforms in [0, 1)."""

    if not addr.distribution_id.startswith("uniform01@"):
        raise ValueError("address distribution_id must be uniform01@<version>")
    raw = raw_uint64(addr, period_ordinal, draw_index, n)
    return (raw >> np.uint64(11)).astype(np.float64) * _TWO_POW_M53


def normal(addr: StreamAddress, period_ordinal: int, draw_index: int, n: int = 1) -> np.ndarray:
    """Return canonical Box-Muller normal draws."""

    if not addr.distribution_id.startswith("normal@"):
        raise ValueError("address distribution_id must be normal@<version>")
    raw = raw_uint64(addr, period_ordinal, draw_index, 2 * n)
    uniforms = ((raw >> np.uint64(11)).astype(np.float64) + 1.0) * _TWO_POW_M53
    first, second = uniforms[0::2], uniforms[1::2]
    return np.sqrt(-2.0 * np.log(first)) * np.cos(2.0 * math.pi * second)


class StreamCollisionError(RuntimeError):
    """Raised when distinct owners claim the same semantic address."""


class StreamRegistry:
    """Claim every semantic address and emit deterministic manifest entries."""

    def __init__(self) -> None:
        self._owners: dict[bytes, str] = {}

    def claim(self, addr: StreamAddress, owner: str) -> StreamAddress:
        key = addr.canonical()
        previous = self._owners.get(key)
        if previous is not None and previous != owner:
            raise StreamCollisionError(f"{addr} already owned by {previous!r}, requested by {owner!r}")
        self._owners[key] = owner
        return addr

    def manifest(self) -> list[dict[str, str]]:
        return [
            {"address": key.decode(), "owner": owner, "transform_version": TRANSFORM_VERSION}
            for key, owner in sorted(self._owners.items())
        ]


def semantic_rng_world_id(world_id: str, seed: int) -> str:
    """Bridge the public world selector and seed into the V3 internal identity."""

    return json.dumps([world_id, int(seed)], ensure_ascii=True, separators=(",", ":"))


class SemanticRNG:
    """Factory and registry owner for explicit World V2 semantic streams."""

    def __init__(self, world_id: str, seed: int, registry: StreamRegistry | None = None) -> None:
        self.world_id = world_id
        self.seed = int(seed)
        self.rng_world_id = semantic_rng_world_id(world_id, seed)
        self.registry = registry or StreamRegistry()

    def stream(self, entity_id: str, mechanism_id: str) -> SemanticStream:
        return SemanticStream(self, entity_id, mechanism_id)

    def manifest(self) -> list[dict[str, str]]:
        return self.registry.manifest()


@dataclass(frozen=True)
class SemanticStream:
    """A non-mutable view that binds calls to one entity and mechanism."""

    context: SemanticRNG
    entity_id: str
    mechanism_id: str

    def _address(self, variable: str, distribution_id: str) -> StreamAddress:
        return self.context.registry.claim(
            StreamAddress(
                world_id=self.context.rng_world_id,
                entity_id=self.entity_id,
                mechanism_id=self.mechanism_id,
                variable=variable,
                distribution_id=distribution_id,
            ),
            owner=self.mechanism_id,
        )

    def raw(self, variable: str, distribution_id: str, period_ordinal: int, draw_index: int = 0) -> int:
        return int(raw_uint64(self._address(variable, distribution_id), period_ordinal, draw_index, 1)[0])

    def uniform(self, variable: str, period_ordinal: int, draw_index: int = 0) -> float:
        addr = self._address(variable, f"uniform01@{TRANSFORM_VERSION}")
        return float(uniform01(addr, period_ordinal, draw_index, 1)[0])

    def uniform_range(
        self, variable: str, lo: float, hi: float, period_ordinal: int, draw_index: int = 0
    ) -> float:
        return lo + (hi - lo) * self.uniform(variable, period_ordinal, draw_index)

    def normal(self, variable: str, period_ordinal: int, draw_index: int = 0) -> float:
        addr = self._address(variable, f"normal@{TRANSFORM_VERSION}")
        return float(normal(addr, period_ordinal, draw_index, 1)[0])

    def lognormal(
        self, variable: str, mean: float, sigma: float, period_ordinal: int, draw_index: int = 0
    ) -> float:
        return math.exp(mean + sigma * self.normal(variable, period_ordinal, draw_index))

    def randint(self, variable: str, lo: int, hi: int, period_ordinal: int, draw_index: int = 0) -> int:
        if hi < lo:
            raise ValueError("integer range must have lo <= hi")
        count = hi - lo + 1
        index = min(count - 1, int(self.uniform(variable, period_ordinal, draw_index) * count))
        return lo + index

    def bernoulli(self, variable: str, probability: float, period_ordinal: int, draw_index: int = 0) -> bool:
        return self.uniform(variable, period_ordinal, draw_index) < probability

    def pick(self, variable: str, items: Sequence[Any], period_ordinal: int, draw_index: int = 0) -> Any:
        if not items:
            raise ValueError("cannot pick from an empty sequence")
        return items[self.randint(variable, 0, len(items) - 1, period_ordinal, draw_index)]

    def state(self) -> dict[str, str]:
        return {
            "namespace": NAMESPACE_VERSION,
            "transform_version": TRANSFORM_VERSION,
            "entity_id": self.entity_id,
            "mechanism_id": self.mechanism_id,
        }


__all__ = [
    "NAMESPACE_VERSION",
    "TRANSFORM_VERSION",
    "TRANSFORM_VERSIONS",
    "SemanticRNG",
    "SemanticStream",
    "StreamAddress",
    "StreamCollisionError",
    "StreamRegistry",
    "normal",
    "raw_uint64",
    "semantic_rng_world_id",
    "uniform01",
]
