from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np

from app.break_test.metrics import backtest_metrics, compute_equity_curve
from app.break_test.regimes import detect_regimes
from app.break_test.strategies import compute_positions


def _feature_vector(prices: np.ndarray, window: int = 20) -> np.ndarray:
    clipped = prices[-(window + 1) :] if len(prices) > window + 1 else prices
    returns = np.diff(np.log(clipped)) if len(clipped) > 1 else np.array([0.0], dtype=float)
    vol = float(np.std(returns, ddof=1)) * math.sqrt(252) if len(returns) > 1 else 0.0
    drift = float(np.mean(returns) * 252) if len(returns) else 0.0
    rolling_var = (
        np.convolve(returns.astype(float) ** 2, np.ones(min(20, len(returns)), dtype=float) / min(20, len(returns)), mode="valid")
        if len(returns) >= 2
        else np.array([0.0], dtype=float)
    )
    rolling_std = np.sqrt(np.maximum(rolling_var, 1e-20))
    if len(rolling_std) == 0:
        high_vol_frac = 0.0
    else:
        median_std = float(np.median(rolling_std))
        high_vol_frac = float(np.mean(rolling_std > (1.5 * median_std))) if median_std > 1e-12 else 0.0
    return np.array([vol / 0.5, drift / 0.5, high_vol_frac / 50.0], dtype=float)


def _cosine_weight(current: np.ndarray, candidate: np.ndarray) -> float:
    norm_c = float(np.linalg.norm(current))
    norm_x = float(np.linalg.norm(candidate))
    if norm_c == 0.0 or norm_x == 0.0:
        return 1.0
    return float(np.dot(current, candidate) / (norm_c * norm_x))


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _sharpe_ci(sharpe: float, n: int) -> tuple[float, float]:
    if n <= 1:
        return float(sharpe), float(sharpe)
    std_err = math.sqrt(1.0 / (n - 1))
    margin = 1.96 * std_err
    return float(sharpe - margin), float(sharpe + margin)


def _psr_vs_zero(sharpe: float, n: int) -> float:
    if n <= 1 or math.isnan(sharpe):
        return 0.0
    z = sharpe * math.sqrt(n)
    denom = math.sqrt(max(1e-12, 1.0 - (sharpe ** 2) / n * (n - 1) / n))
    adjusted_z = z * denom
    return float(_norm_cdf(adjusted_z))


def _psr_vs_benchmark(strategy_sharpe: float, benchmark_sharpe: float, n: int, covariance: float | None = None) -> float:
    if n <= 1 or math.isnan(strategy_sharpe) or math.isnan(benchmark_sharpe):
        return 0.0
    diff = strategy_sharpe - benchmark_sharpe
    std_diff = math.sqrt(max(1e-12, 1.0 + 1.0 / max(n - 1, 1) - 2 * (covariance if covariance is not None else 0.0)))
    z = diff / std_diff
    return float(_norm_cdf(z))


def _deflated_sharpe(oos_sharpes: list[float]) -> float:
    arr = np.asarray(oos_sharpes, dtype=float)
    k = len(arr)
    if k <= 1:
        return float(np.mean(arr)) if k == 1 else 0.0
    sr = float(np.mean(arr))
    if sr <= 0:
        return 0.0
    denominator = math.sqrt(max(1e-12, sr ** 2 - 1.0 / (k - 1)))
    try:
        z_score = sr / denominator
    except ValueError:
        return 0.0
    percentile = _norm_cdf(z_score)
    shrunk_mean = sr * math.sqrt(1.0 + 1.0 / max(k - 1, 1))
    lower_bound = float(
        shrunk_mean * percentile - math.sqrt(1.0 / (k - 1)) * _norm_cdf(z_score - 1.96 / math.sqrt(k))
    )
    return round(float(np.clip(lower_bound, -5.0, 5.0)), 4)


def _consistency_sharpe(folds: list[dict[str, Any]]) -> float:
    sharpes = [float(f["oos_sharpe"]) for f in folds]
    if len(sharpes) < 2:
        return 0.0
    mean = float(np.mean(sharpes))
    std = float(np.std(sharpes, ddof=1))
    if std == 0.0:
        return float(mean) if mean >= 0 else 0.0
    penalty = math.exp(-max(mean, 0.0))
    return round(mean / std * penalty * math.sqrt(252), 4)


def _benjamini_hochberg(p_values: list[float]) -> list[float]:
    if not p_values:
        return []
    m = len(p_values)
    sorted_pairs = sorted(enumerate(p_values), key=lambda pair: pair[1])
    adjusted: list[float] = [0.0] * m
    min_adj = 1.0
    for rank, (idx, p) in enumerate(sorted_pairs, start=1):
        adj = min(p * m / rank, 1.0)
        min_adj = min(min_adj, adj)
        adjusted[idx] = round(min_adj, 6)
    return adjusted


def _decision_boundary(oos_sharpe: float, deflated_sharpe: float, psr_vs_zero: float, consistency: float) -> str:
    if oos_sharpe < 0 or deflated_sharpe <= 0:
        return "rejected"
    if psr_vs_zero < 0.5 or consistency < 0:
        return "review"
    return "passed"


def _adversarial_mutation(prices: np.ndarray, regime: str, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    mutated = prices.astype(float).copy()
    shock_map = {
        "earnings_shock": (0.05, 0.04, 18),
        "liquidity_withdrawal": (-0.02, 0.06, 24),
        "crowded_unwind": (-0.03, 0.09, 12),
        "rate_hike": (-0.04, 0.05, 21),
    }
    if regime not in shock_map:
        return mutated
    spike, noise, center = shock_map[regime]
    idx = min(center, len(mutated) - 1)
    mutated[idx] *= (1.0 + spike)
    small = rng.normal(0.0, noise, size=len(mutated))
    mutated = mutated * (1.0 + np.clip(small, -0.02, 0.02))
    return np.maximum(mutated, 1e-6)


def adversarial_validation_summary(*args, **kwargs):
    raise NotImplementedError("Use walk_forward_validation with adversarial=True")


def beatable_guard(*args, **kwargs):
    raise NotImplementedError("Use walk_forward_validation or nested CPCV summary")


def parameter_instability_score(*args, **kwargs):
    raise NotImplementedError("Use nested combinatorial_purged_cross_validation param_stability")


def nested_combinatorial_purged_cross_validation(*args, **kwargs):
    raise NotImplementedError("Use combinatorial_purged_cross_validation with nested=True")


def _walk_forward_folds(
    px: np.ndarray,
    train_window: int,
    embargo: int,
    anchored: bool,
    step: int | None,
    test_window: int | None,
    max_folds: int = 24,
) -> list[dict[str, Any]]:
    n = len(px)
    effective_test = max(test_window or step or train_window, 1)
    effective_step = step or effective_test
    folds: list[dict[str, Any]] = []
    start = train_window
    fold_index = 0
    while start + embargo + effective_test <= n and fold_index < max_folds:
        train_start = 0 if anchored else max(0, start - train_window)
        train_end = start
        test_start = start + embargo
        test_end = test_start + effective_test
        if test_end > n:
            break
        folds.append(
            {
                "fold": fold_index,
                "train_start": int(train_start),
                "train_end": int(train_end),
                "test_start": int(test_start),
                "test_end": int(test_end),
                "train_prices": px[train_start:train_end].tolist(),
                "test_prices": px[test_start:test_end].tolist(),
            }
        )
        fold_index += 1
        start += effective_step
    return folds


def walk_forward_validation(
    prices: list[float],
    strategy_type: str,
    params: dict[str, int],
    train_window: int = 120,
    test_window: int | None = None,
    step: int | None = None,
    embargo: int = 5,
    anchored: bool = False,
    regime_aware: bool = False,
    benchmark_returns: list[float] | None = None,
    adversarial: bool = False,
    adversarial_seed: int = 42,
) -> dict[str, Any]:
    px = np.asarray(prices, dtype=float)
    n = len(px)
    if n < 50 or train_window < 2 or (test_window is not None and test_window < 1):
        return _make_empty_result("insufficient data for walk-forward", benchmark_returns is not None)

    effective_test = max(test_window or step or train_window, 1)
    effective_step = step or effective_test
    folds: list[dict[str, Any]] = []

    base_folds = _walk_forward_folds(px, train_window, embargo, anchored, step, test_window)
    for fold in base_folds:
        train_prices = fold["train_prices"]
        test_prices = fold["test_prices"]
        regime_vec = _feature_vector(test_prices)
        try:
            positions = compute_positions(strategy_type, test_prices, **(params or {}))
        except ValueError:
            continue
        if len(positions) != len(test_prices):
            continue
        train_positions = compute_positions(strategy_type, train_prices, **(params or {})) if len(train_prices) >= 5 else np.array([], dtype=float)
        hist = backtest_metrics(train_prices, train_positions) if len(train_prices) > 1 else {"sharpe": 0.0}
        metrics = backtest_metrics(test_prices, positions)
        fold["historical_sharpe"] = round(float(hist["sharpe"]), 3)
        fold["oos_sharpe"] = round(float(metrics["sharpe"]), 4)
        fold["oos_return_pct"] = round(float(metrics["total_return_pct"]), 4)
        fold["oos_max_drawdown_pct"] = round(float(metrics["max_drawdown_pct"]), 4)
        fold["oos_sortino"] = round(float(metrics["sortino"]), 4)
        fold["oos_calmar"] = round(float(metrics["calmar"]), 4)
        fold["oos_trades"] = int(metrics["trades"])
        fold["weight"] = 1.0
        fold["regime_features"] = regime_vec.round(4).tolist()
        fold["equity_curve"] = compute_equity_curve(test_prices, positions)
        if adversarial:
            adversarial_test_prices = _adversarial_mutation(test_prices, "earnings_shock", adversarial_seed + fold["fold"])
            try:
                adversarial_positions = compute_positions(strategy_type, adversarial_test_prices, **(params or {}))
            except ValueError:
                adversarial_positions = np.array([0.0] * len(test_prices))
            adversarial_metrics = backtest_metrics(adversarial_test_prices, adversarial_positions)
            fold["adversarial_oos_sharpe"] = round(float(adversarial_metrics["sharpe"]), 4)
            fold["adversarial_drawdown"] = round(float(adversarial_metrics["max_drawdown_pct"]), 4)
        folds.append(fold)

    if not folds:
        return _make_empty_result("no valid OOS windows", benchmark_returns is not None)

    current_vec = _feature_vector(px) if regime_aware else np.array([0.0, 0.0, 0.0], dtype=float)
    similarities = [_cosine_weight(current_vec, np.array(fold["regime_features"])) for fold in folds]
    total_sim = sum(similarities)
    weights = [s / total_sim for s in similarities] if total_sim > 0 else [1.0 / len(folds)] * len(folds)
    for fold, weight in zip(folds, weights):
        fold["weight"] = round(float(weight), 6)
        fold["regime_weight"] = round(float(weight), 6)

    summary = _summarise_folds(folds, embargo, anchored, benchmark_returns=benchmark_returns)
    summary["adversarial"] = True if adversarial and folds else False
    return summary


def compute_default_params(strategy_type: str) -> dict[str, float]:
    if strategy_type == "sma_crossover":
        return {"fast": 20, "slow": 50}
    if strategy_type == "breakout":
        return {"entry_lookback": 20, "exit_lookback": 10}
    if strategy_type == "rsi_reversion":
        return {"period": 14, "oversold": 30, "overbought": 70}
    return {}


def _make_empty_result(note: str, has_benchmark: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "folds": [],
        "oos_sharpe": 0.0,
        "deflated_sharpe": 0.0,
        "psr_vs_zero": 0.0,
        "consistency_sharpe": 0.0,
        "parameter_stability": 0.0,
        "current_regime": {},
        "regime_weights": [],
        "n_folds": 0,
        "train_window": 0,
        "step": 0,
        "embargo": 0,
        "anchored": False,
        "note": note,
    }
    if has_benchmark:
        result["psr_vs_benchmark"] = 0.0
        result["relative_sharpe_vs_benchmark"] = 0.0
    zero_deflated_conditions = result["n_folds"] == 0 and result["deflated_sharpe"] == 0.0
    if zero_deflated_conditions:
        result["negative_guard_warning"] = (
            "deflated_sharpe is 0.00 because 0 valid folds were produced; "
            "this is NOT evidence of strategy performance. Increase data length, relax embargo, "
            "or check strategy/parameter compatibility."
        )
    return result


def _make_benchmark_positions(prices: np.ndarray, strategy_type: str) -> np.ndarray:
    if strategy_type == "sma_crossover":
        if len(prices) < 200:
            return np.zeros(len(prices), dtype=float)
        ma = np.convolve(prices, np.ones(200) / 200, mode="valid")
        signal = np.zeros(len(prices), dtype=float)
        aligned = np.concatenate((np.zeros(199, dtype=float), (prices[199:] > ma).astype(float)))
        return aligned
    if strategy_type == "momentum":
        if len(prices) < 60:
            return np.zeros(len(prices), dtype=float)
        lookback = 20
        returns = np.concatenate((np.zeros(lookback, dtype=float), np.diff(prices) / np.maximum(prices[:-1], 1e-12)))
        momentum = np.convolve(returns, np.ones(lookback) / lookback, mode="full")[: len(prices)]
        signal = np.clip(momentum * 252, -1.0, 1.0)
        return signal
    return np.zeros(len(prices), dtype=float)


def _beatable_guard(
    folds: list[dict[str, Any]],
    strategy_type: str,
    benchmark_returns: list[float] | None,
) -> tuple[bool, str]:
    if not folds or benchmark_returns is None or len(benchmark_returns) < 10:
        return True, "guard skipped: insufficient benchmark data"
    prices = np.linspace(100, 150, len(benchmark_returns))
    positions = _make_benchmark_positions(prices, strategy_type)
    if len(positions) != len(benchmark_returns):
        positions = np.concatenate((positions, np.zeros(len(benchmark_returns) - len(positions), dtype=float)))[: len(benchmark_returns)]
    returns = np.diff(np.array(benchmark_returns, dtype=float)) / np.maximum(np.array(benchmark_returns[:-1], dtype=float), 1e-12)
    bench_returns = returns
    bench_sharpe = float(np.mean(bench_returns) / np.std(bench_returns, ddof=1) * math.sqrt(252)) if len(bench_returns) > 1 and float(np.std(bench_returns, ddof=1)) > 0 else 0.0
    strategy_sharpes = [float(f["oos_sharpe"]) for f in folds]
    if np.mean(strategy_sharpes) <= bench_sharpe:
        return False, f"strategy mean oos sharpe {round(float(np.mean(strategy_sharpes)),3)} <= simple benchmark sharpe {round(bench_sharpe,3)}"
    return True, f"strategy mean oos sharpe {round(float(np.mean(strategy_sharpes)),3)} > simple benchmark sharpe {round(bench_sharpe,3)}"


def _score_candidate(prices: np.ndarray, strategy_type: str, candidate: dict[str, int], embargo: int) -> float:
    if len(prices) < max(60, candidate.get("slow", 50) + embargo + 4):
        return -1e9
    folds = _walk_forward_folds(prices, train_window=min(90, len(prices) // 3), embargo=embargo, anchored=False, step=None, test_window=None)
    if len(folds) < 2:
        return -1e9
    sharpes: list[float] = []
    for fold in folds:
        try:
            positions = compute_positions(strategy_type, fold["test_prices"], **candidate)
        except ValueError:
            return -1e9
        metrics = backtest_metrics(fold["test_prices"], positions)
        sharpes.append(float(metrics["sharpe"]))
    if not sharpes:
        return -1e9
    mean = float(np.mean(sharpes))
    std = float(np.std(sharpes, ddof=1))
    return mean / std if std > 0 else (mean if mean > 0 else -1e9)


def _select_nested_params(prices: np.ndarray, strategy_type: str, param_ranges: dict[str, tuple[int, int]], embargo: int) -> dict[str, int] | None:
    if not param_ranges or prices.size < 60:
        return None
    candidates: list[tuple[float, dict[str, int]]] = []
    keys = list(param_ranges.keys())
    for values in itertools.product(*[range(*param_ranges[k]) for k in keys]):
        candidate = dict(zip(keys, values))
        score = _score_candidate(prices, strategy_type, candidate, embargo)
        if score > -1e9:
            candidates.append((score, candidate))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def combinatorial_purged_cross_validation(
    prices: list[float],
    strategy_type: str,
    params: dict[str, int],
    blocks: int = 4,
    embargo: int = 5,
    max_combinations: int = 16,
    block_width: int = 30,
    benchmark_returns: list[float] | None = None,
    nested: bool = False,
    param_ranges: dict[str, tuple[int, int]] | None = None,
    adversarial: bool = False,
    adversarial_seed: int = 42,
) -> dict[str, Any]:
    px = np.asarray(prices, dtype=float)
    n = len(px)
    if n < 50 or blocks < 2:
        summary = _make_empty_result("insufficient data or blocks for CPCV", benchmark_returns is not None)
        if benchmark_returns is not None:
            summary["psr_vs_benchmark"] = 0.0
            summary["relative_sharpe_vs_benchmark"] = 0.0
        summary["blocks_evaluated"] = 0
        summary["combinations_evaluated"] = 0
        return summary

    effective_embargo = max(embargo, int(math.ceil(0.01 * n)))
    effective_blocks = max(blocks, 2)
    candidate_max_combos = [max_combinations, max(max_combinations // 2, 2), max(max_combinations // 4, 2), 1]
    candidate_blocks = [effective_blocks, max(effective_blocks - 1, 2), max(effective_blocks // 2, 2)]
    folds: list[dict[str, Any]] = []
    produced_combos: list[tuple[int, ...]] = []
    produced_config_note = None

    for trial_blocks in candidate_blocks:
        for trial_max_combos in candidate_max_combos:
            trial_folds: list[dict[str, Any]] = []
            block_size = max(n // trial_blocks, block_width)
            combos = list(itertools.combinations(range(trial_blocks), min(trial_blocks, 3)))[:trial_max_combos]
            for combo in combos:
                train_mask = np.ones(n, dtype=bool)
                test_mask = np.zeros(n, dtype=bool)
                for b in combo:
                    start = b * block_size
                    end = min(start + block_size, n)
                    embargo_start = max(0, start - effective_embargo)
                    train_mask[embargo_start:end] = False
                    test_mask[start:end] = True
                train_prices = px[train_mask]
                test_prices = px[test_mask]
                if not len(test_prices) or not len(train_prices):
                    continue

                candidate_params = params or {}
                if nested:
                    selected = _select_nested_params(train_prices, strategy_type, param_ranges or {}, effective_embargo)
                    if selected is None:
                        continue
                    candidate_params = selected

                try:
                    t_pos = compute_positions(strategy_type, test_prices, **candidate_params)
                    tr_pos = compute_positions(strategy_type, train_prices, **candidate_params)
                except ValueError:
                    continue
                if len(t_pos) != len(test_prices) or len(tr_pos) != len(train_prices):
                    continue
                fold_metrics = backtest_metrics(test_prices, t_pos)
                hist = backtest_metrics(train_prices, tr_pos)
                regime_vec = _feature_vector(test_prices)
                fold: dict[str, Any] = {
                    "combo": list(combo),
                    "trial_blocks": trial_blocks,
                    "trial_max_combinations": trial_max_combos,
                    "train_start": int(np.flatnonzero(train_mask)[0]) if np.any(train_mask) else 0,
                    "train_end": int(np.flatnonzero(train_mask)[-1] + 1) if np.any(train_mask) else 0,
                    "test_start": int(np.flatnonzero(test_mask)[0]) if np.any(test_mask) else 0,
                    "test_end": int(np.flatnonzero(test_mask)[-1] + 1) if np.any(test_mask) else 0,
                    "embargo": int(effective_embargo),
                    "historical_sharpe": round(float(hist["sharpe"]), 3),
                    "oos_sharpe": round(float(fold_metrics["sharpe"]), 4),
                    "oos_return_pct": round(float(fold_metrics["total_return_pct"]), 4),
                    "oos_max_drawdown_pct": round(float(fold_metrics["max_drawdown_pct"]), 4),
                    "oos_trades": int(fold_metrics["trades"]),
                    "weight": 1.0,
                    "regime_features": regime_vec.round(4).tolist(),
                    "equity_curve": compute_equity_curve(test_prices, t_pos),
                    "params": candidate_params,
                }
                if adversarial:
                    adversarial_test_prices = _adversarial_mutation(test_prices, "earnings_shock", adversarial_seed + len(trial_folds))
                    try:
                        adversarial_positions = compute_positions(strategy_type, adversarial_test_prices, **candidate_params)
                    except ValueError:
                        adversarial_positions = np.array([0.0] * len(test_prices))
                    adversarial_metrics = backtest_metrics(adversarial_test_prices, adversarial_positions)
                    fold["adversarial_oos_sharpe"] = round(float(adversarial_metrics["sharpe"]), 4)
                    fold["adversarial_drawdown"] = round(float(adversarial_metrics["max_drawdown_pct"]), 4)
                trial_folds.append(fold)
            if trial_folds:
                folds = trial_folds
                produced_combos = [tuple(c) for c in combos]
                produced_config_note = (
                    f"CPCV sparse pruning activated: requested blocks={blocks}, combinations={max_combinations}, "
                    f"but used blocks={trial_blocks}, combinations={trial_max_combos} to produce valid folds."
                )
                break
        if folds:
            break

    if not folds:
        summary = _make_empty_result("no valid CPCV combinations after pruning", benchmark_returns is not None)
        if benchmark_returns is not None:
            summary["psr_vs_benchmark"] = 0.0
            summary["relative_sharpe_vs_benchmark"] = 0.0
        summary["blocks_evaluated"] = candidate_blocks[-1]
        summary["combinations_evaluated"] = 0
        return summary

    oos_sharpes = np.array([float(f["oos_sharpe"]) for f in folds], dtype=float)
    p_values = [max(0.0, 1.0 - _psr_vs_zero(float(s), len(folds))) for s in oos_sharpes]
    adjusted_p_values = _benjamini_hochberg(p_values)
    strategy_params = [f.get("params", {}) for f in folds if f.get("params")]
    param_stability = _parameter_stability_correlation(strategy_params)
    beatable, beat_note = _beatable_guard(folds, strategy_type, benchmark_returns)
    overall_sharpe = float(np.mean(oos_sharpes)) if oos_sharpes.size else 0.0
    decision = _decision_boundary(overall_sharpe, _deflated_sharpe([float(f["oos_sharpe"]) for f in folds]), _psr_vs_zero(overall_sharpe, len(folds)), _consistency_sharpe(folds))
    summary = _summarise_folds(folds, effective_embargo, False, benchmark_returns=benchmark_returns)
    summary["blocks"] = blocks
    summary["blocks_evaluated"] = folds[0]["trial_blocks"]
    summary["combinations_evaluated"] = len(produced_combos)
    summary["combinations_attempted"] = len(list(itertools.combinations(range(folds[0]["trial_blocks"]), min(folds[0]["trial_blocks"], 3))))
    summary["nested"] = nested
    summary["param_stability"] = param_stability
    summary["bh_adjusted_p_values"] = adjusted_p_values
    summary["beatable"] = beatable
    summary["beatable_note"] = beat_note
    summary["multiple_testing_correction"] = "benjamini_hochberg"
    if nested and param_ranges:
        summary["selected_params"] = folds[0].get("params", params)
    if not beatable or decision == "rejected":
        summary["verdict"] = "likely spurious"
    else:
        summary["verdict"] = decision
    if adversarial:
        summary["adversarial_oos_sharpes"] = [round(float(f.get("adversarial_oos_sharpe", 0.0)), 4) for f in folds]
        summary["adversarial_drawdowns"] = [round(float(f.get("adversarial_drawdown", 0.0)), 4) for f in folds]
        summary["verdict"] = "fragile"
    if produced_config_note:
        summary["note"] = produced_config_note
    else:
        summary["note"] = "Nested CPCV with embargo-purged overlap; BH-adjusted p-values and multicollinear stability guards applied."
    return summary


def _benchmark_summary(folds: list[dict[str, Any]], benchmark_returns: list[float] | None) -> dict[str, Any] | None:
    if benchmark_returns is None:
        return None
    bench = np.asarray(benchmark_returns, dtype=float)
    if bench.size == 0:
        return None
    bench_returns = np.diff(bench) / bench[:-1] if bench.size > 1 else np.array([0.0], dtype=float)
    bench_sharpe_full = float(np.mean(bench_returns) / np.std(bench_returns, ddof=1) * math.sqrt(252)) if len(bench_returns) > 1 and float(np.std(bench_returns, ddof=1)) > 0 else 0.0
    strategy_sharpes = [float(f["oos_sharpe"]) for f in folds]
    n = len(bench_returns)
    min_len = min(len(folds), n)
    cov_sum = 0.0
    if min_len > 1:
        combos = list(zip(strategy_sharpes, [r for i, r in enumerate(bench_returns) if i < min_len]))
        if len(combos) > 1:
            a = np.array([c[0] for c in combos], dtype=float)
            b = np.array([c[1] for c in combos], dtype=float)
            cov_sum = float(np.cov(a, b, ddof=1)[0, 1])
    psr_bench = _psr_vs_benchmark(float(np.mean(strategy_sharpes)), bench_sharpe_full, min_len, covariance=cov_sum if min_len > 1 else None)
    return {
        "benchmark_sharpe": round(bench_sharpe_full, 4),
        "strategy_mean_sharpe": round(float(np.mean(strategy_sharpes)), 4),
        "psr_vs_benchmark": psr_bench,
        "relative_sharpe_vs_benchmark": round(float(np.mean(strategy_sharpes) - bench_sharpe_full), 4),
    }


def _parameter_stability_correlation(param_sets: list[dict[str, int]]) -> float:
    if len(param_sets) < 2:
        return 0.0
    keys = sorted({k for p in param_sets for k in p.keys()})
    vectors = [np.array([float(p.get(k, 0)) for k in keys], dtype=float) for p in param_sets]
    correlations: list[float] = []
    for a, b in zip(vectors[:-1], vectors[1:]):
        if np.std(a) == 0 or np.std(b) == 0:
            continue
        corr = float(np.corrcoef(a, b)[0, 1])
        if not math.isnan(corr):
            correlations.append(corr)
    if not correlations:
        return 0.0
    return round(float(np.mean(correlations)), 4)


def _parameter_stability(folds: list[dict[str, Any]], embargo: int) -> float:
    if len(folds) < 2:
        return 0.0
    sharpes = np.array([float(f["oos_sharpe"]) for f in folds], dtype=float)
    mean = float(np.mean(sharpes))
    std = float(np.std(sharpes, ddof=1))
    if mean <= 0 or std <= 0:
        return 0.0
    cv = std / abs(mean)
    stability = 1.0 / (1.0 + cv)
    return round(float(np.clip(stability, 0.0, 1.0)), 4)


def _summarise_folds(folds: list[dict[str, Any]], embargo: int, anchored: bool, benchmark_returns: list[float] | None = None) -> dict[str, Any]:
    oos_sharpes = np.array([float(f["oos_sharpe"]) for f in folds], dtype=float)
    oos_sharpe = float(np.mean(oos_sharpes)) if oos_sharpes.size else 0.0
    n_folds = len(folds)
    weighted_sharpes = np.array([float(f.get("weight", 1.0)) * float(f["oos_sharpe"]) for f in folds], dtype=float)
    weights = np.array([float(f.get("weight", 1.0)) for f in folds], dtype=float)
    weighted_sharpe = float(np.sum(weighted_sharpes) / np.sum(weights)) if np.sum(weights) > 0 else 0.0
    regime_targets = [float(f.get("equity_curve", [0.0])[-1]) if f.get("equity_curve") else 0.0 for f in folds]
    current_regime = detect_regimes(regime_targets)
    regime_weights = [round(float(f.get("weight", 1.0 / max(n_folds, 1))), 6) for f in folds]
    deflated = _deflated_sharpe([float(f["oos_sharpe"]) for f in folds])
    result: dict[str, Any] = {
        "folds": folds,
        "oos_sharpe": round(oos_sharpe, 4),
        "weighted_oos_sharpe": round(weighted_sharpe, 4),
        "oos_fold_sharpes": [round(float(x), 4) for x in oos_sharpes.tolist()],
        "oos_confidence_interval": _sharpe_ci(oos_sharpe, n_folds),
        "deflated_sharpe": deflated,
        "psr_vs_zero": _psr_vs_zero(oos_sharpe, n_folds),
        "consistency_sharpe": _consistency_sharpe(folds),
        "parameter_stability": _parameter_stability(folds, embargo),
        "n_folds": n_folds,
        "regime_weights": regime_weights,
        "current_regime": current_regime,
        "train_window": int(folds[0]["train_end"] - folds[0]["train_start"]) if folds else 0,
        "step": int(folds[1]["test_start"] - folds[0]["test_start"]) if len(folds) > 1 else 0,
        "embargo": int(embargo),
        "anchored": anchored,
        "note": "Walk-forward uses embargoed OOS evaluation; deflated Sharpe follows Bailey & López de Prado (2014).",
    }
    if n_folds == 0 and deflated == 0.0:
        result["negative_guard_warning"] = (
            "deflated_sharpe is 0.00 because 0 valid folds were produced; "
            "this is NOT evidence of strategy performance. Increase data length, relax embargo, "
            "or check strategy/parameter compatibility."
        )
    bench_summary = _benchmark_summary(folds, benchmark_returns)
    if bench_summary is not None:
        result.update(bench_summary)
    return result
