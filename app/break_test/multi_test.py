"""Multiple-testing corrections: White Reality Check, SPA, and MCS."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def _stationary_bootstrap_indices(n: int, rng: np.random.Generator, block_p: float = 0.1) -> np.ndarray:
    indices = np.empty(n, dtype=int)
    indices[0] = int(rng.integers(0, n))
    for t in range(1, n):
        if rng.random() < block_p:
            indices[t] = int(rng.integers(0, n))
        else:
            indices[t] = (indices[t - 1] + 1) % n
    return indices


def white_reality_check(
    strategy_returns_list: Sequence[np.ndarray | Sequence[float]],
    benchmark_returns: np.ndarray | Sequence[float] | None = None,
    *,
    n_bootstrap: int = 500,
    seed: int = 0,
) -> dict[str, object]:
    """White (2000) Reality Check on relative mean returns vs benchmark (or zero)."""
    rng = np.random.default_rng(seed)
    mats = [np.asarray(r, dtype=float).reshape(-1) for r in strategy_returns_list]
    if not mats:
        return {"p_value": 1.0, "statistic": 0.0, "n_strategies": 0}
    n = min(len(m) for m in mats)
    mats = [m[:n] for m in mats]
    if benchmark_returns is None:
        bench = np.zeros(n, dtype=float)
    else:
        bench = np.asarray(benchmark_returns, dtype=float).reshape(-1)[:n]
    diffs = np.column_stack([m - bench for m in mats])
    mean_d = diffs.mean(axis=0)
    stat = float(np.max(mean_d))
    boot_max = np.empty(n_bootstrap, dtype=float)
    for b in range(n_bootstrap):
        idx = _stationary_bootstrap_indices(n, rng)
        centered = diffs[idx] - mean_d
        boot_max[b] = float(np.max(centered.mean(axis=0)))
    p_value = float(np.mean(boot_max >= stat - 1e-15))
    return {
        "p_value": round(p_value, 6),
        "statistic": round(stat, 8),
        "n_strategies": len(mats),
        "method": "white_reality_check",
    }


def spa_test(
    strategy_returns_list: Sequence[np.ndarray | Sequence[float]],
    benchmark_returns: np.ndarray | Sequence[float] | None = None,
    *,
    n_bootstrap: int = 500,
    seed: int = 1,
) -> dict[str, object]:
    """Hansen (2005) SPA-style test with studentized max statistic."""
    rng = np.random.default_rng(seed)
    mats = [np.asarray(r, dtype=float).reshape(-1) for r in strategy_returns_list]
    if not mats:
        return {"p_value": 1.0, "statistic": 0.0, "n_strategies": 0}
    n = min(len(m) for m in mats)
    mats = [m[:n] for m in mats]
    bench = (
        np.zeros(n, dtype=float)
        if benchmark_returns is None
        else np.asarray(benchmark_returns, dtype=float).reshape(-1)[:n]
    )
    diffs = np.column_stack([m - bench for m in mats])
    mean_d = diffs.mean(axis=0)
    std_d = diffs.std(axis=0, ddof=1)
    std_d = np.where(std_d < 1e-12, 1e-12, std_d)
    t_stat = mean_d / (std_d / math.sqrt(n))
    # Hansen recentering: keep only non-negative consistent performers.
    consistent = np.where(mean_d > -np.sqrt(np.log(max(len(mean_d), 2)) / n) * std_d, mean_d, 0.0)
    stat = float(np.max(t_stat))
    boot = np.empty(n_bootstrap, dtype=float)
    for b in range(n_bootstrap):
        idx = _stationary_bootstrap_indices(n, rng)
        sample = diffs[idx]
        m = sample.mean(axis=0) - consistent
        s = sample.std(axis=0, ddof=1)
        s = np.where(s < 1e-12, 1e-12, s)
        boot[b] = float(np.max(m / (s / math.sqrt(n))))
    p_value = float(np.mean(boot >= stat - 1e-15))
    return {
        "p_value": round(p_value, 6),
        "statistic": round(stat, 6),
        "n_strategies": len(mats),
        "method": "spa",
    }


def mcs_selection(
    strategy_returns_list: Sequence[np.ndarray | Sequence[float]],
    *,
    n_bootstrap: int = 400,
    alpha: float = 0.1,
    seed: int = 2,
) -> dict[str, object]:
    """Hansen-Lunde-Nason MCS: iteratively drop worst models by t-stat."""
    rng = np.random.default_rng(seed)
    mats = [np.asarray(r, dtype=float).reshape(-1) for r in strategy_returns_list]
    if not mats:
        return {"included": [], "p_values": {}, "method": "mcs"}
    n = min(len(m) for m in mats)
    losses = np.column_stack([-m[:n] for m in mats])  # lower loss = better
    remaining = list(range(losses.shape[1]))
    p_values: dict[int, float] = {}
    while len(remaining) > 1:
        sub = losses[:, remaining]
        mean_loss = sub.mean(axis=0)
        # Relative to best (lowest loss) in the set.
        best = int(np.argmin(mean_loss))
        d = sub - sub[:, best : best + 1]
        t_stats = []
        for j in range(d.shape[1]):
            if j == best:
                t_stats.append(-np.inf)
                continue
            col = d[:, j]
            se = float(np.std(col, ddof=1)) / math.sqrt(n)
            t_stats.append(float(mean_loss[j] - mean_loss[best]) / max(se, 1e-12))
        worst_local = int(np.argmax(t_stats))
        # Bootstrap p for range statistic.
        range_stat = float(max(t_stats))
        boot = np.empty(n_bootstrap, dtype=float)
        for b in range(n_bootstrap):
            idx = _stationary_bootstrap_indices(n, rng)
            sample = sub[idx]
            m = sample.mean(axis=0)
            b_best = int(np.argmin(m))
            rel = m - m[b_best]
            se = sample.std(axis=0, ddof=1) / math.sqrt(n)
            se = np.where(se < 1e-12, 1e-12, se)
            boot[b] = float(np.max(rel / se))
        p = float(np.mean(boot >= range_stat - 1e-15))
        victim = remaining[worst_local]
        p_values[victim] = round(p, 6)
        if p >= alpha:
            break
        remaining.pop(worst_local)
    for idx in remaining:
        p_values.setdefault(idx, 1.0)
    return {
        "included": remaining,
        "p_values": p_values,
        "alpha": alpha,
        "method": "mcs",
    }
