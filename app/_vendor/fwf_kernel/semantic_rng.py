"""SEMANTIC_RNG_V3 — address-keyed, order-independent random draws.

Every random number in a world is addressed by *meaning*, not by call order:

    address = (namespace_version, world_id, entity_id, mechanism_id, variable, distribution_id)
    key     = first 128 bits of SHA-256(canonical_json(address))  -> Philox 2x64 key
    counter = (0, period_ordinal, draw_index, 0)                  -> Philox 4x64 counter

The first counter word is reserved for NumPy Philox's internal block advancement. A
multi-block ``random_raw(n)`` call can therefore never collide with another period or draw.
Semantic substreams belong in ``distribution_id`` rather than the advancement word.

Consequences:
  * Adding an entity, a mechanism, or a variable never shifts any other stream.
  * Reordering generation code never changes outputs.
  * Two mechanisms cannot silently share a stream: `StreamRegistry.claim` rejects a second owner.

Reproducibility contract (see NumPy's random compatibility policy):
  * `raw_uint64` is the bit-exact layer. Golden vectors are asserted on it exactly.
  * Transforms are versioned (TRANSFORM_VERSION). `uniform01` uses only integer shifts and one
    exact multiply, so it is bit-exact everywhere. `normal` uses log/sqrt/cos from the platform
    libm; sqrt is correctly rounded by IEEE-754 but log/cos are not guaranteed identical across
    platforms, so golden vectors for `normal` are compared within 4 ulp.
  * We never call NumPy's Generator distribution methods, because their streams may change in
    NumPy feature releases; only the BitGenerator's raw output is relied upon.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

import numpy as np

NAMESPACE_VERSION = "SEMANTIC_RNG_V3"
TRANSFORM_VERSION = "T1"
_TWO_POW_M53 = 2.0**-53


@dataclass(frozen=True)
class StreamAddress:
    world_id: str
    entity_id: str
    mechanism_id: str
    variable: str
    distribution_id: (
        str  # e.g. "uniform01@T1", "normal@T1"; changing a distribution changes the stream
    )

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
        d = hashlib.sha256(self.canonical()).digest()
        return np.array(
            [int.from_bytes(d[0:8], "little"), int.from_bytes(d[8:16], "little")], dtype=np.uint64
        )


def raw_uint64(addr: StreamAddress, period_ordinal: int, draw_index: int, n: int = 1) -> np.ndarray:
    """Bit-exact raw draws. Same (addr, period, draw_index, n) -> same bits, on any machine."""
    if period_ordinal < 0 or draw_index < 0 or n < 1:
        raise ValueError("period_ordinal and draw_index must be >= 0 and n >= 1")
    # NumPy advances counter[0] internally when random_raw crosses a four-uint64 block.
    # Keep semantic coordinates above it so n > 4 cannot overlap another address.
    counter = np.array([0, period_ordinal, draw_index, 0], dtype=np.uint64)
    bg = np.random.Philox(key=addr.key(), counter=counter)
    return bg.random_raw(n)


def uniform01(addr: StreamAddress, period_ordinal: int, draw_index: int, n: int = 1) -> np.ndarray:
    """Exact 53-bit uniforms in [0, 1). Bit-exact across platforms."""
    if not addr.distribution_id.startswith("uniform01@"):
        raise ValueError("address distribution_id must be uniform01@<version>")
    r = raw_uint64(addr, period_ordinal, draw_index, n)
    return (r >> np.uint64(11)).astype(np.float64) * _TWO_POW_M53


def normal(addr: StreamAddress, period_ordinal: int, draw_index: int, n: int = 1) -> np.ndarray:
    """Box-Muller over 2n raw draws. Deterministic; cross-platform equality within a few ulp."""
    if not addr.distribution_id.startswith("normal@"):
        raise ValueError("address distribution_id must be normal@<version>")
    r = raw_uint64(addr, period_ordinal, draw_index, 2 * n)
    u = ((r >> np.uint64(11)).astype(np.float64) + 1.0) * _TWO_POW_M53  # (0, 1], avoids log(0)
    u1, u2 = u[0::2], u[1::2]
    return np.sqrt(-2.0 * np.log(u1)) * np.cos(2.0 * math.pi * u2)


class StreamCollisionError(RuntimeError):
    pass


class StreamRegistry:
    """Every mechanism must claim its addresses; a second owner of the same address is an error."""

    def __init__(self) -> None:
        self._owners: dict[bytes, str] = {}

    def claim(self, addr: StreamAddress, owner: str) -> StreamAddress:
        k = addr.canonical()
        prev = self._owners.get(k)
        if prev is not None and prev != owner:
            raise StreamCollisionError(f"{addr} already owned by {prev!r}, requested by {owner!r}")
        self._owners[k] = owner
        return addr

    def manifest(self) -> list[dict[str, str]]:
        """Deterministic listing for WorldManifestV3.stream_registry."""
        return [
            {"address": k.decode(), "owner": v, "transform_version": TRANSFORM_VERSION}
            for k, v in sorted(self._owners.items())
        ]
