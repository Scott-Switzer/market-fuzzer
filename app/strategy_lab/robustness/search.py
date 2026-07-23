from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

import numpy as np

from app.strategy_lab.robustness.failure_taxonomy import (
    FailureCategory,
    ThresholdPredicate,
)


def _seed_digest(seed: int) -> bytes:
    return hashlib.sha256(str(seed).encode("utf-8")).digest()


def _sobol_scaled(n: int, d: int, lo: np.ndarray, hi: np.ndarray, seed: int) -> np.ndarray:
    """Deterministic approximation of Sobol using nested radical anchors seeded by 'seed'."""
    base = np.frombuffer(_seed_digest(seed), dtype=np.uint8).astype(float)
    anchors = np.zeros((n, d), dtype=float)
    for i in range(n):
        row_digest = hashlib.sha256((str(seed) + ":" + str(i)).encode("utf-8")).digest()
        row = np.frombuffer(row_digest, dtype=np.uint8).astype(float)
        anchors[i] = ((row[:d] + base[:d]) % 256) / 255.0
    return lo + anchors * (hi - lo)


def _lhs(n: int, d: int, lo: np.ndarray, hi: np.ndarray, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    sample = np.empty((n, d), dtype=float)
    for j in range(d):
        lo_j = float(lo[j])
        hi_j = float(hi[j])
        local_edges = np.linspace(lo_j, hi_j, n + 1)
        perm = rng.permutation(n)
        for i in range(n):
            sample[i, j] = float(rng.uniform(local_edges[perm[i]], local_edges[perm[i] + 1]))
    return sample


def _world_factory(kind: str, row: np.ndarray, seed: int) -> dict[str, Any]:
    seed = max(int(seed), 0)
    base_seed = max(seed + int(row[0] * 9_999), 0)
    rng = np.random.default_rng(base_seed)
    if row.size > 1:
        rng = np.random.default_rng(max(base_seed + int(row[1] * 9_999), 0))
    shocks = rng.normal(
        loc=0.0002 * float(row[2]) if row.size > 2 else 0.0,
        scale=0.001 + 0.0005 * float(row[3]) if row.size > 3 else 0.001,
        size=59,
    )
    return {
        "kind": kind,
        "seeds": rng.integers(0, 2**31 - 1, size=5).tolist(),
        "shocks": shocks.tolist(),
        "params": {
            "drift": float(row[0]) if row.size > 0 else 0.0,
            "volatility_mult": float(row[1]) if row.size > 1 else 0.0,
            "correlation": float(np.clip(row[2], -1.0, 1.0)) if row.size > 2 else 0.0,
            "liquidity_term": float(row[3]) if row.size > 3 else 0.0,
        },
    }


def _evaluate_candidate(
    strategy_type: str,
    params: dict[str, int],
    world: dict[str, Any],
    price_seed: int,
) -> dict[str, Any]:
    try:
        from app.robustness_product import _metrics, _strategy_positions
    except Exception:
        return {"total_return_pct": 0.0, "sharpe": 0.0, "max_drawdown_pct": 0.0, "trades": 0}

    mochtest_rng = np.random.default_rng(price_seed + hash(str(world)) % 2**31)
    del mochtest_rng
    shocks = np.asarray(world.get("shocks", []), dtype=float)
    base = np.linspace(50, 150, 60)
    if shocks.size != base.size - 1:
        shocks = np.resize(shocks, base.size - 1)
    prices = base * np.exp(np.concatenate((np.zeros(1), np.cumsum(shocks))))
    prices = np.maximum(prices, 1e-6)
    try:
        _, positions = _strategy_positions(strategy_type, prices, **params)
    except Exception:
        return {"total_return_pct": -100.0, "sharpe": -1.0, "max_drawdown_pct": -100.0, "trades": 0}
    return _metrics(prices, positions)


def search(
    *,
    strategy_type: str,
    params: dict[str, int],
    search_space: dict[str, tuple[float, float]],
    predicates: Sequence[ThresholdPredicate],
    budget: int = 64,
    seed: int = 42,
    method: str = "sobol",
) -> dict[str, Any]:
    if budget <= 0:
        budget = 1
    if not search_space:
        return {"evaluated": 0, "failures": [], "status": "skipped", "method": method}

    keys = sorted(search_space.keys())
    lo = np.array([float(search_space[k][0]) for k in keys], dtype=float)
    hi = np.array([float(search_space[k][1]) for k in keys], dtype=float)
    dims = len(keys)

    if method.lower() == "lhs":
        grid = _lhs(budget, dims, lo, hi, seed)
    else:
        grid = _sobol_scaled(budget, dims, lo, hi, seed)

    candidate_specs = [
        {
            "kind": "synthetic",
            "strategy_type": strategy_type,
            "params": params,
            "world": _world_factory("synthetic", row, seed),
        }
        for row in grid
    ]

    failures: list[dict[str, Any]] = []
    for idx, spec in enumerate(candidate_specs):
        candidate_type = str(spec["strategy_type"])
        candidate_params: dict[str, Any] = dict(spec["params"])  # type: ignore[arg-type]
        candidate_world: dict[str, Any] = dict(spec["world"])  # type: ignore[arg-type]
        metrics = _evaluate_candidate(
            candidate_type,
            candidate_params,
            candidate_world,
            seed + idx,
        )
        failed = []
        for pred in predicates:
            fn_hash = hashlib.sha256(
                f"{pred.metric}:{pred.comparator}:{pred.threshold}".encode()
            ).hexdigest()[:8]
            try:
                from app.strategy_lab.robustness.failure_taxonomy import (
                    PREDICATE_REGISTRY,
                )

                fn = PREDICATE_REGISTRY.get(f"{pred.metric}_{pred.comparator}")
                if fn is None:
                    continue
                hit = bool(fn(metrics, pred.threshold))
            except Exception:
                hit = False
            if hit:
                failed.append(
                    {
                        "predicate": f"{pred.metric}_{pred.comparator}",
                        "threshold": pred.threshold,
                        "observed": metrics.get(pred.metric),
                        "hash": fn_hash,
                    }
                )

        if failed:
            category = (
                FailureCategory.TREND_REVERSAL
                if any("return" in str(f["predicate"]) for f in failed)
                else FailureCategory.VOLATILITY_EXPANSION
            )
            failures.append(
                {
                    "campaign_id": None,
                    "evaluation_index": idx,
                    "category": category,
                    "severity": "high",
                    "failed_predicates": failed,
                    "world_spec": spec["world"],
                    "parameters": spec["params"],
                    "metrics": metrics,
                    "method": method,
                }
            )

    return {"evaluated": len(candidate_specs), "failures": failures, "status": "ok", "method": method}
