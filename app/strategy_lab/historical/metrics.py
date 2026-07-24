from __future__ import annotations

import math
from typing import Any


class HistoricalMetricsEngine:
    @staticmethod
    def _safe(value: float | Any) -> float | Any:
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value

    @staticmethod
    def _sanitize(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: HistoricalMetricsEngine._sanitize(value) for key, value in item.items()}
        if isinstance(item, list):
            return [HistoricalMetricsEngine._sanitize(v) for v in item]
        if isinstance(item, float) and not math.isfinite(item):
            return None
        return item

    @staticmethod
    def _safe_dict(mapping: dict[str, Any]) -> dict[str, Any]:
        return {key: HistoricalMetricsEngine._sanitize(value) for key, value in mapping.items()}

    @staticmethod
    def _norm_cdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    @staticmethod
    def _norm_ppf(p: float) -> float:
        if p <= 0.0:
            return -float("inf")
        if p >= 1.0:
            return float("inf")
        if p < 0.5:
            return -HistoricalMetricsEngine._norm_ppf(1.0 - p)
        try:
            import numpy as np

            return float(np.percentile(np.random.standard_normal(1_000_000), p * 100))
        except Exception:
            return HistoricalMetricsEngine._norm_ppf_fallback(p)

    @staticmethod
    def _norm_ppf_fallback(p: float) -> float:
        return (
            1.959963984540054
            if abs(p - 0.975) < 0.001
            else 1.6448536269514722
            if abs(p - 0.95) < 0.001
            else 1.2815515655446004
        )

    @staticmethod
    def compute(
        equity_curve: list[float],
        risk_free: float = 0.0,
        prices: list[float] | None = None,
        positions: list[float] | None = None,
        params: dict[str, int] | None = None,
        strategy_type: str | None = None,
        benchmark_prices: list[float] | None = None,
        trial_count: int | None = None,
        bootstrap_seed: int = 7,
    ) -> dict[str, Any]:
        if prices is not None and positions is not None:
            import numpy as np

            from app.break_test.metrics import backtest_metrics, compute_equity_curve

            px = np.asarray(prices, dtype=float)
            pos = np.asarray(positions, dtype=float)
            out: dict[str, Any] = {"status": "ok"}
            out.update(backtest_metrics(px, pos))
            curve = compute_equity_curve(px, pos)
        else:
            out = {"status": "ok"}
            curve = [float(v) for v in equity_curve]

        returns: list[float] = []
        if len(curve) > 1:
            returns = [curve[i] / curve[i - 1] - 1.0 for i in range(1, len(curve))]
            out["equity_return_series"] = [HistoricalMetricsEngine._safe(round(r, 6)) for r in returns]
            mean = sum(returns) / len(returns)
            variance = sum((r - mean) ** 2 for r in returns) / max(len(returns) - 1, 1)
            std = variance**0.5
            cagr = (curve[-1] - 1.0) * 252.0 / max(len(returns), 1)
            out["cagr"] = HistoricalMetricsEngine._safe(cagr)
            out["volatility"] = HistoricalMetricsEngine._safe(std * (252.0**0.5))
            sharpe = (mean / std * (252.0**0.5)) if std > 0 else 0.0
            out["sharpe"] = HistoricalMetricsEngine._safe(sharpe)
            downside = [r - mean for r in returns if r < mean]
            downside_variance = sum(r * r for r in downside) / max(len(downside), 1)
            sortino = (mean / (downside_variance**0.5) * (252.0**0.5)) if downside_variance > 0 else 0.0
            out["sortino"] = HistoricalMetricsEngine._safe(sortino)
            peaks = [max(curve[: i + 1]) for i in range(len(curve))]
            dd = [c / p - 1.0 for c, p in zip(curve, peaks, strict=True)]
            max_drawdown = min(dd) if dd else 0.0
            out["max_drawdown"] = HistoricalMetricsEngine._safe(max_drawdown)
            out["calmar"] = HistoricalMetricsEngine._safe(
                (cagr / abs(max_drawdown)) if max_drawdown < 0 else (cagr if cagr > 0 else 0.0)
            )
            out["calmar_warning"] = (
                "Calmar can be misleading when max_drawdown is near zero or the return path is very short."
            )

            wins = [r for r in returns if r > 0]
            losses = [r for r in returns if r < 0]
            out["hit_rate"] = HistoricalMetricsEngine._safe(len(wins) / len(returns) if returns else 0.0)
            out["hit_rate_warning"] = (
                "Hit rate ignores magnitude; a strategy can have a low hit rate and still be profitable."
            )
            gross_losses = abs(sum(losses))
            profit_factor = (sum(wins) / gross_losses) if gross_losses > 0 else (math.inf if wins else 0.0)
            out["profit_factor"] = HistoricalMetricsEngine._safe(profit_factor)
            out["profit_factor_warning"] = (
                "Profit factor is unstable with very few trades and treats gains/losses symmetrically."
            )

            out["turnover"] = None
            out["concentration"] = None
            out["gross_exposure"] = None
            out["net_exposure"] = None
            if positions is not None:
                p = [float(x) for x in positions]
                if len(p) > 1:
                    out["turnover"] = HistoricalMetricsEngine._safe(
                        float(sum(abs(p[i] - p[i - 1]) for i in range(1, len(p))))
                    )
                concentration = sum(weight * weight for weight in p) / max(len(p), 1)
                out["concentration"] = HistoricalMetricsEngine._safe(concentration)
                out["concentration_warning"] = (
                    "Concentration uses a single positional series; for multi-asset portfolios provide per-asset weights."
                )
                avg_abs = sum(abs(w) for w in p) / max(len(p), 1)
                out["gross_exposure"] = HistoricalMetricsEngine._safe(avg_abs)
                out["gross_exposure_warning"] = (
                    "Gross exposure is a time-averaged absolute exposure, not a single-period snapshot."
                )
                out["net_exposure"] = HistoricalMetricsEngine._safe(sum(p) / max(len(p), 1))
                out["net_exposure_warning"] = (
                    "Net exposure is time-averaged signed exposure and can understate intraday risk."
                )

            out["var_95"] = None
            out["cvar_95"] = None
            out["var_99"] = None
            out["cvar_99"] = None
            out["var_cvar_warning"] = (
                "VaR/CVaR are historical Gaussian-free percentiles and assume the future distribution resembles the sample."
            )
            if returns:
                try:
                    import numpy as np

                    rets = np.asarray(returns, dtype=float)
                    var_95 = float(np.percentile(rets, 5))
                    cvar_95 = float(np.mean(rets[rets <= var_95])) if rets.size else 0.0
                    var_99 = float(np.percentile(rets, 1))
                    cvar_99 = float(np.mean(rets[rets <= var_99])) if rets.size else 0.0
                    out["var_95"] = HistoricalMetricsEngine._safe(var_95)
                    out["cvar_95"] = HistoricalMetricsEngine._safe(cvar_95)
                    out["var_99"] = HistoricalMetricsEngine._safe(var_99)
                    out["cvar_99"] = HistoricalMetricsEngine._safe(cvar_99)
                except Exception:
                    out["var_95"] = 0.0
                    out["cvar_95"] = 0.0
                    out["var_99"] = 0.0
                    out["cvar_99"] = 0.0

            out["bootstrap_sharpe_ci"] = None
            out["bootstrap_cagr_ci"] = None
            out["bootstrap_warning"] = (
                "Bootstrap CIs are planned-not-implemented when numpy.random is unavailable."
            )
            try:
                boot = HistoricalMetricsEngine._bootstrap_ci(returns, seed=bootstrap_seed)
                out["bootstrap_sharpe_ci"] = {
                    "estimate": HistoricalMetricsEngine._safe(boot["sharpe"]["estimate"]),
                    "ci_low": HistoricalMetricsEngine._safe(boot["sharpe"]["ci_low"]),
                    "ci_high": HistoricalMetricsEngine._safe(boot["sharpe"]["ci_high"]),
                    "n_bootstrap": 1000,
                }
                out["bootstrap_cagr_ci"] = {
                    "estimate": HistoricalMetricsEngine._safe(boot["cagr"]["estimate"]),
                    "ci_low": HistoricalMetricsEngine._safe(boot["cagr"]["ci_low"]),
                    "ci_high": HistoricalMetricsEngine._safe(boot["cagr"]["ci_high"]),
                    "n_bootstrap": 1000,
                }
                out["bootstrap_warning"] = (
                    "Bootstrap CIs use 1 000 block-bootstrap resamples; widen intervals if returns are autocorrelated."
                )
            except Exception:
                out["bootstrap_warning"] = (
                    "Bootstrap CIs are unavailable because numpy.random/bootstrap routine raised an exception."
                )

            out["psr"] = None
            out["psr_threshold"] = None
            out["psr_warning"] = None
            if sharpe is not None and returns:
                alpha = 0.05
                if trial_count is not None and trial_count > 0:
                    threshold = HistoricalMetricsEngine._norm_ppf(1.0 - alpha / float(trial_count))
                    out["psr_threshold"] = HistoricalMetricsEngine._safe(threshold)
                    out["psr_warning"] = (
                        "PSR threshold is Bonferroni-adjusted for the supplied trial_count; Holm-Bonferroni would be tighter."
                    )
                else:
                    threshold = HistoricalMetricsEngine._norm_ppf(1.0 - alpha)
                    out["psr_threshold"] = HistoricalMetricsEngine._safe(threshold)
                    out["psr_warning"] = (
                        "PSR threshold is plain 5% quantile because trial_count was not supplied."
                    )
                denom = math.sqrt(max(1.0 - (float(sharpe) ** 2) / 4.0, 1e-12))
                stat = (float(sharpe) - threshold) * math.sqrt(max(len(returns) - 1, 1)) / denom
                out["psr"] = HistoricalMetricsEngine._safe(HistoricalMetricsEngine._norm_cdf(stat))

        if benchmark_prices:
            b = [float(v) for v in benchmark_prices]
            bench_curve = [b[i] / b[i - 1] for i in range(1, len(b))] if len(b) > 1 else []
            bench_returns = (
                [bench_curve[i] / bench_curve[i - 1] - 1.0 for i in range(1, len(bench_curve))]
                if len(bench_curve) > 1
                else []
            )
            bench_mean = sum(bench_returns) / len(bench_returns) if bench_returns else 0.0
            bench_var = (
                sum((r - bench_mean) ** 2 for r in bench_returns) / max(len(bench_returns) - 1, 1)
                if bench_returns
                else 0.0
            )
            bench_std = bench_var**0.5
            bench_cagr = bench_mean * 252.0
            bench_sharpe = (bench_mean / bench_std * (252.0**0.5)) if bench_std > 0 else 0.0
            out["benchmark_cagr"] = HistoricalMetricsEngine._safe(bench_cagr)
            out["benchmark_sharpe"] = HistoricalMetricsEngine._safe(bench_sharpe)
            out["benchmark_warning"] = (
                "Benchmark metrics assume the supplied price series is a clean total-return benchmark; fees/tracking error are ignored."
            )
            if returns and bench_returns:
                try:
                    min_len = min(len(returns), len(bench_returns))
                    aligned = [
                        r - br for r, br in zip(returns[:min_len], bench_returns[:min_len], strict=False)
                    ]
                    if aligned:
                        aligned_mean = sum(aligned) / len(aligned)
                        aligned_var = (
                            sum((x - aligned_mean) ** 2 for x in aligned) / max(len(aligned) - 1, 1)
                            if len(aligned) > 1
                            else 0.0
                        )
                        aligned_std = aligned_var**0.5
                        out["information_ratio"] = HistoricalMetricsEngine._safe(
                            (aligned_mean / aligned_std * (252.0**0.5)) if aligned_std > 0 else 0.0
                        )
                        out["information_ratio_warning"] = (
                            "Information ratio assumes the supplied benchmark is the sole factor and ignores trading costs."
                        )
                except Exception:
                    out["information_ratio"] = 0.0

        return HistoricalMetricsEngine._safe_dict(out)

    @staticmethod
    def _bootstrap_ci(
        returns: list[float],
        *,
        n_bootstrap: int = 1000,
        alpha: float = 0.05,
        seed: int = 7,
    ) -> dict[str, dict[str, float]]:
        import numpy as np

        rng = np.random.default_rng(seed)
        rets = np.asarray(returns, dtype=float)
        if rets.size < 5:
            return {
                "sharpe": {"estimate": 0.0, "ci_low": 0.0, "ci_high": 0.0},
                "cagr": {"estimate": 0.0, "ci_low": 0.0, "ci_high": 0.0},
            }

        def _annualised(r: np.ndarray) -> tuple[float, float]:
            mean = float(np.mean(r))
            std = float(np.std(r, ddof=1)) if r.size > 1 else 0.0
            sharpe = (mean / std * math.sqrt(252)) if std > 0 else 0.0
            cagr = mean * 252.0
            return sharpe, cagr

        observed = _annualised(rets)
        sharp_boots = np.empty(n_bootstrap, dtype=float)
        cagr_boots = np.empty(n_bootstrap, dtype=float)
        block = max(5, rets.size // 20)
        for i in range(n_bootstrap):
            starts = rng.integers(0, max(rets.size - block, 1), size=max(1, rets.size // block + 1))
            sample = np.concatenate([rets[s : s + block] for s in starts])[: rets.size]
            sharp_boots[i], cagr_boots[i] = _annualised(sample)
        lo_sharp, hi_sharp = np.percentile(sharp_boots, [100 * alpha / 2, 100 * (1 - alpha / 2)])
        lo_cagr, hi_cagr = np.percentile(cagr_boots, [100 * alpha / 2, 100 * (1 - alpha / 2)])
        return {
            "sharpe": {
                "estimate": round(float(observed[0]), 6),
                "ci_low": round(float(lo_sharp), 6),
                "ci_high": round(float(hi_sharp), 6),
            },
            "cagr": {
                "estimate": round(float(observed[1]), 6),
                "ci_low": round(float(lo_cagr), 6),
                "ci_high": round(float(hi_cagr), 6),
            },
        }
