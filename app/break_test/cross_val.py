"""Purged / embargoed cross-validation helpers."""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np


def _norm_ppf(p: float) -> float:
    p = min(max(float(p), 1e-12), 1.0 - 1e-12)
    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577459334128e02,
        -3.066479806614736e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580577e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def block_bounds(n: int, n_blocks: int) -> list[tuple[int, int]]:
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


def combinatorial_purged_cv(
    prices: Sequence[float],
    *,
    k: int = 5,
    n_test_blocks: int = 2,
    embargo: int = 5,
    anchored: bool = True,
    max_combinations: int = 16,
    score_fn: Callable[[np.ndarray, np.ndarray], float] | None = None,
    positions_fn: Callable[[np.ndarray], np.ndarray] | None = None,
) -> dict[str, Any]:
    """Combinatorial purged cross-validation with embargo periods.

    Generates all feasible block combinations for ``k`` >= 3 assets/time-series
    blocks, honoring embargo windows around test blocks to prevent lookahead
    bias.
    """
    px = np.asarray(prices, dtype=float)
    n = len(px)
    k = max(3, min(int(k), max(n // 10, 3)))
    embargo = max(0, int(embargo))
    max_combinations = max(1, int(max_combinations))
    bounds = block_bounds(n, k)
    n_blocks = len(bounds)
    if n_blocks < n_test_blocks + 1:
        return {
            "folds": [],
            "k": k,
            "n_blocks": n_blocks,
            "n_test_blocks": n_test_blocks,
            "embargo": embargo,
            "anchored": anchored,
            "combinations_attempted": 0,
            "exhaustiveness": False,
            "note": "insufficient blocks for combinatorial split",
            "n_folds": 0,
            "mean_oos_sharpe": 0.0,
        }

    def _default_positions(p: np.ndarray) -> np.ndarray:
        return np.ones(len(p), dtype=float)

    def _default_score(p: np.ndarray, pos: np.ndarray) -> float:
        if len(p) < 3:
            return 0.0
        rets = np.diff(p) / np.clip(p[:-1], 1e-9, None)
        strat = rets * pos[:-1]
        std = float(np.std(strat, ddof=1)) if len(strat) > 1 else 0.0
        if std <= 0:
            return 0.0
        return float(np.mean(strat) / std * np.sqrt(252))

    pos_fn = positions_fn or _default_positions
    scorer = score_fn or _default_score

    combos = cpcv_combinations(n_blocks, n_test_blocks)[:max_combinations]
    folds: list[dict[str, Any]] = []
    for combo in combos:
        test_mask = np.zeros(n, dtype=bool)
        train_mask = np.ones(n, dtype=bool)
        train_no_embargo = np.ones(n, dtype=bool)
        for b in combo:
            start, end = bounds[b]
            test_mask[start:end] = True
            embargo_start = max(0, start - embargo)
            purge_end = min(n, end + embargo)
            train_mask[embargo_start:purge_end] = False
            train_no_embargo[start:end] = False
        if anchored:
            train_mask[: int(np.flatnonzero(test_mask)[0]) if np.any(test_mask) else 0] &= train_no_embargo[
                : int(np.flatnonzero(test_mask)[0]) if np.any(test_mask) else 0
            ]
        else:
            train_mask = np.logical_and(train_mask, np.logical_not(test_mask))
        if int(np.sum(train_mask)) < 5 or int(np.sum(test_mask)) < 2:
            continue
        test_px = px[test_mask]
        test_pos = pos_fn(test_px)
        score = scorer(test_px, test_pos)
        folds.append(
            {
                "combo": list(combo),
                "train_size": int(np.sum(train_mask)),
                "test_size": int(np.sum(test_mask)),
                "oos_sharpe": round(float(score), 6),
                "test_start": int(np.flatnonzero(test_mask)[0]),
                "test_end": int(np.flatnonzero(test_mask)[-1] + 1),
            }
        )

    sharpes = [f["oos_sharpe"] for f in folds]
    return {
        "folds": folds,
        "k": k,
        "n_blocks": n_blocks,
        "n_test_blocks": n_test_blocks,
        "embargo": embargo,
        "anchored": anchored,
        "combinations_attempted": len(combos),
        "exhaustiveness": len(combos) == len(cpcv_combinations(n_blocks, n_test_blocks)),
        "mean_oos_sharpe": round(float(np.mean(sharpes)), 6) if sharpes else 0.0,
        "n_folds": len(folds),
        "note": "CPCV with embargo-purged train/test isolation",
    }


def holm_bonferroni_threshold(
    sharpe_series: Sequence[float],
    *,
    n_trials: int | None = None,
    alpha: float = 0.05,
) -> float:
    """Step-down Holm-Bonferroni threshold on trial Sharpe ratios."""
    arr = np.asarray(sharpe_series, dtype=float)
    trials = max(1, int(n_trials) if n_trials is not None else int(arr.size))
    if arr.size == 0:
        return 0.0
    p_values = [
        max(0.0, 1.0 - _norm_cdf(float(s) / max(math.sqrt(252.0 / max(arr.size, 1)), 1e-9)))
        for s in arr.tolist()
    ]
    sorted_pairs = sorted(enumerate(p_values), key=lambda pair: pair[1])
    min_adj = 1.0
    threshold = 0.0
    for rank, (idx, p) in enumerate(sorted_pairs, start=1):
        adj = min(p * (trials - rank + 1), 1.0)
        min_adj = min(min_adj, adj)
        if min_adj < alpha:
            threshold = float(arr[idx])
            break
    return threshold


def extreme_value_sr(n_trials: int) -> float:
    """Expected maximum Sharpe under the null for n_trials."""
    trials = max(1, int(n_trials))
    ppf = _norm_ppf
    e_max = (1.0 - np.euler_gamma) * ppf(1.0 - 1.0 / trials) + np.euler_gamma * ppf(
        1.0 - 1.0 / (trials * math.e)
    )
    return float(e_max)


def purged_k_fold_cv(
    prices: np.ndarray | list[float],
    *,
    k: int = 5,
    embargo: int = 5,
    anchored: bool = True,
    score_fn: Callable[[np.ndarray, np.ndarray], float] | None = None,
    positions_fn: Callable[[np.ndarray], np.ndarray] | None = None,
) -> dict[str, Any]:
    """Purged K-fold CV with embargo gaps around test blocks.

    When ``positions_fn`` is omitted, uses buy-and-hold as a diagnostic baseline.
    """
    px = np.asarray(prices, dtype=float)
    n = len(px)
    k = max(2, min(int(k), max(n // 10, 2)))
    embargo = max(0, int(embargo))
    fold_size = max(1, n // k)
    folds: list[dict[str, Any]] = []

    def _default_positions(p: np.ndarray) -> np.ndarray:
        return np.ones(len(p), dtype=float)

    def _default_score(p: np.ndarray, pos: np.ndarray) -> float:
        if len(p) < 3:
            return 0.0
        rets = np.diff(p) / np.clip(p[:-1], 1e-9, None)
        strat = rets * pos[:-1]
        std = float(np.std(strat, ddof=1)) if len(strat) > 1 else 0.0
        if std <= 0:
            return 0.0
        return float(np.mean(strat) / std * np.sqrt(252))

    pos_fn = positions_fn or _default_positions
    scorer = score_fn or _default_score

    for fold_i in range(k):
        test_start = fold_i * fold_size
        test_end = n if fold_i == k - 1 else min(n, (fold_i + 1) * fold_size)
        purge_start = max(0, test_start - embargo)
        purge_end = min(n, test_end + embargo)
        if anchored:
            train_idx = np.arange(0, purge_start)
        else:
            train_idx = np.concatenate([np.arange(0, purge_start), np.arange(purge_end, n)])
        test_idx = np.arange(test_start, test_end)
        if train_idx.size < 5 or test_idx.size < 2:
            continue
        test_px = px[test_idx]
        test_pos = pos_fn(test_px)
        score = scorer(test_px, test_pos)
        folds.append(
            {
                "fold": fold_i,
                "train_size": int(train_idx.size),
                "test_size": int(test_idx.size),
                "oos_sharpe": round(float(score), 6),
                "test_start": int(test_start),
                "test_end": int(test_end),
            }
        )
    sharpes = [f["oos_sharpe"] for f in folds]
    return {
        "folds": folds,
        "k": k,
        "embargo": embargo,
        "anchored": anchored,
        "mean_oos_sharpe": round(float(np.mean(sharpes)), 6) if sharpes else 0.0,
        "n_folds": len(folds),
    }
