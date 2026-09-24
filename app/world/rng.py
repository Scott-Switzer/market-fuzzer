"""Deterministic, seed-derived RNG streams for world construction.

Every stochastic decision in the world must come from a named stream
derived from (seed, stream_name) so that worlds are byte-identical across
runs and machines, and so that interventions cannot perturb unrelated
streams (each stream is independent).
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Sequence
from typing import Any

import numpy as np


def _stable_hash(*parts: object) -> int:
    joined = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).digest()
    return struct.unpack(">Q", digest[:8])[0]


def conform_seed(seed: int, *parts: object) -> int:
    """Map (seed, *parts) to a stable non-negative 63-bit numpy seed."""
    return _stable_hash(seed, *parts) & ((1 << 63) - 1)


class SeedStream:
    """A named, replayable stream of draws backed by numpy's PCG64."""

    def __init__(self, seed: int, name: str) -> None:
        self._rng = np.random.default_rng(conform_seed(seed, name))
        self.name = name
        self.draws = 0

    def normal(self) -> float:
        self.draws += 1
        return float(self._rng.standard_normal())

    def uniform(self) -> float:
        self.draws += 1
        return float(self._rng.uniform())

    def uniform_range(self, lo: float, hi: float) -> float:
        self.draws += 1
        return float(self._rng.uniform(lo, hi))

    def lognormal(self, mean: float = 0.0, sigma: float = 1.0) -> float:
        self.draws += 1
        return float(self._rng.lognormal(mean, sigma))

    def randint(self, lo: int, hi: int) -> int:
        """Inclusive integer draw in [lo, hi]."""
        self.draws += 1
        return int(self._rng.integers(lo, hi + 1))

    def bernoulli(self, p: float) -> bool:
        self.draws += 1
        return bool(self._rng.random() < p)

    def pick(self, items: Sequence[str]) -> str:
        self.draws += 1
        return items[self.randint(0, len(items) - 1)]

    def state(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "draws": self.draws,
            "bit_generator": type(self._rng.bit_generator).__name__,
        }


def derive_stream(seed: int, name: str) -> SeedStream:
    """Derive an independent named stream from a world seed."""
    return SeedStream(seed, name)
