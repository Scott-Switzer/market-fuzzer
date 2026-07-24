"""Overfitting bounds: Flajolet-Karlin SDB and López de Prado DSB."""

from __future__ import annotations

import math

import numpy as np


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(float(x) / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Approximate inverse CDF via Beasley-Springer-Moro style rational approx."""
    p = min(max(float(p), 1e-12), 1.0 - 1e-12)
    # Ackley/Hart approximation
    a = [2.50662823884, -18.61500062529, 41.39119773534, -25.44106049637]
    b = [-8.47351093090, 23.08336743743, -21.06224101826, 3.13082909833]
    c = [
        0.3374754822726147,
        0.9761690190917186,
        0.1607979322276188,
        0.0276438810333863,
        0.0038405729373609,
        0.0003951896511919,
        0.0000321767881768,
        0.0000002888167364,
        0.0000003960708379,
    ]
    y = p - 0.5
    if abs(y) < 0.42:
        r = y * y
        num = a[0] + r * (a[1] + r * (a[2] + r * a[3]))
        den = 1.0 + r * (b[0] + r * (b[1] + r * (b[2] + r * b[3])))
        return y * num / den
    r = p if y > 0 else 1.0 - p
    s = math.log(-math.log(r))
    t = c[0] + s * (
        c[1] + s * (c[2] + s * (c[3] + s * (c[4] + s * (c[5] + s * (c[6] + s * (c[7] + s * c[8]))))))
    )
    return t if y > 0 else -t


def flajolet_karlin_sdb(
    n_strategies: int,
    k_candidates: int,
    sharpe: float,
    *,
    n_obs: int = 252,
) -> dict[str, float]:
    """Flajolet–Karlin style Sharpe deflation bound for multiple testing."""
    n = max(1, int(n_strategies))
    k = max(1, int(k_candidates))
    trials = max(n * k, 1)
    z_expected_max = math.sqrt(2.0 * math.log(trials)) if trials > 1 else 0.0
    se = 1.0 / math.sqrt(max(n_obs, 2))
    threshold = z_expected_max * se * math.sqrt(252)
    excess = float(sharpe) - threshold
    se_ann = se * math.sqrt(252)
    z = excess / max(se_ann, 1e-12)
    p_spurious = float(1.0 - _norm_cdf(z))
    return {
        "sdb_threshold": round(threshold, 6),
        "observed_sharpe": round(float(sharpe), 6),
        "p_spurious": round(max(0.0, min(1.0, p_spurious)), 6),
        "n_trials": float(trials),
    }


def deprado_dsb(
    past_returns: np.ndarray | list[float],
    n_candidates: int,
    sharpe: float,
) -> dict[str, float]:
    """López de Prado Deflated Sharpe Bound / Probabilistic Sharpe proxy."""
    rets = np.asarray(past_returns, dtype=float).reshape(-1)
    n = max(len(rets), 2)
    k = max(int(n_candidates), 1)
    sr = float(sharpe)
    if n > 3 and float(np.std(rets)) > 0:
        m2 = float(np.mean((rets - rets.mean()) ** 2))
        m3 = float(np.mean((rets - rets.mean()) ** 3))
        m4 = float(np.mean((rets - rets.mean()) ** 4))
        skew = m3 / max(m2**1.5, 1e-16)
        kurt = m4 / max(m2**2, 1e-16)
    else:
        skew, kurt = 0.0, 3.0
    sr0 = 0.0
    if k > 1:
        e_max = (1.0 - np.euler_gamma) * _norm_ppf(1.0 - 1.0 / k) + np.euler_gamma * _norm_ppf(
            1.0 - 1.0 / (k * math.e)
        )
        e_max = float(e_max) / math.sqrt(n) * math.sqrt(252)
    else:
        e_max = 0.0
    v = (1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2) / max(n - 1, 1)
    v = max(v, 1e-12)
    z = (sr - max(sr0, e_max)) / math.sqrt(v)
    p_significant = float(_norm_cdf(z))
    return {
        "dsb": round(float(sr - e_max), 6),
        "expected_max_null_sharpe": round(e_max, 6),
        "p_significant": round(max(0.0, min(1.0, p_significant)), 6),
        "n_candidates": float(k),
        "observed_sharpe": round(sr, 6),
    }
