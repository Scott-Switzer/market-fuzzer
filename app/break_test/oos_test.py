"""CPCV block helpers extracted for integrity / exhaustiveness checks."""

from __future__ import annotations

import itertools
from collections.abc import Iterator


def cpcv_block_bounds(n: int, n_blocks: int) -> list[tuple[int, int]]:
    n_blocks = max(2, int(n_blocks))
    width = max(1, n // n_blocks)
    bounds: list[tuple[int, int]] = []
    for i in range(n_blocks):
        start = i * width
        end = n if i == n_blocks - 1 else min(n, (i + 1) * width)
        if end > start:
            bounds.append((start, end))
    return bounds


def cpcv_combinations(n_blocks: int, n_test_blocks: int = 2) -> list[tuple[int, ...]]:
    n_blocks = max(2, int(n_blocks))
    n_test = max(1, min(int(n_test_blocks), n_blocks - 1))
    return list(itertools.combinations(range(n_blocks), n_test))


def iter_cpcv_combinations(n_blocks: int, n_test_blocks: int = 2) -> Iterator[tuple[int, ...]]:
    yield from cpcv_combinations(n_blocks, n_test_blocks)


def combinations_attempted_count(n_blocks: int, n_test_blocks: int = 2) -> int:
    return len(cpcv_combinations(n_blocks, n_test_blocks))


def exhaustiveness_flag(n_blocks: int) -> bool:
    """When blocks <= 5, exhaustive combination enumeration is feasible."""
    return int(n_blocks) <= 5
