"""Robustness / quant-validation unit tests (Hours 11)."""

from __future__ import annotations

import numpy as np

from app.break_test.attribution import attribution_by_regime, conditional_performance_attribution
from app.break_test.cross_val import purged_k_fold_cv
from app.break_test.metrics import bootstrap_metric_ci
from app.break_test.multi_test import mcs_selection, spa_test, white_reality_check
from app.break_test.oos_test import combinations_attempted_count, exhaustiveness_flag
from app.break_test.oos_validation import _deflated_sharpe, bias_corrected_sharpe
from app.break_test.overfit_bounds import deprado_dsb, flajolet_karlin_sdb
from app.break_test.quant_report import rank_strategies
from app.break_test.synthetic_market import ResearchSyntheticMarketGenerator


def _prices(n: int = 300) -> np.ndarray:
    rng = np.random.default_rng(0)
    return np.cumprod(1 + rng.normal(0.0004, 0.01, n)) * 100


def test_bootstrap_metric_ci_shape() -> None:
    px = _prices()
    pos = np.ones_like(px)
    ci = bootstrap_metric_ci(px, pos, fn="sharpe", n_bootstrap=40)
    assert ci["ci_low"] <= ci["ci_high"]


def test_multi_test_family_runs() -> None:
    rng = np.random.default_rng(1)
    strats = [rng.normal(0.0005, 0.01, 80) for _ in range(3)]
    bench = rng.normal(0.0002, 0.01, 80)
    assert "p_value" in white_reality_check(strats, bench, n_bootstrap=40)
    assert "p_value" in spa_test(strats, bench, n_bootstrap=40)
    assert "included" in mcs_selection(strats, n_bootstrap=40)


def test_deflated_sharpe_and_bias_corrected() -> None:
    dsr = _deflated_sharpe([0.2, 0.4, 0.3, 0.5, 0.1])
    assert 0.0 <= dsr <= 1.0
    bc = bias_corrected_sharpe(np.random.default_rng(0).normal(0.001, 0.01, 200), n_bootstrap=40)
    assert "bias_corrected_sharpe" in bc


def test_overfit_bounds() -> None:
    sdb = flajolet_karlin_sdb(5, 10, 1.2)
    assert "p_spurious" in sdb
    dsb = deprado_dsb(np.random.default_rng(0).normal(0.001, 0.01, 100), 20, 1.0)
    assert "dsb" in dsb


def test_cpcv_helpers_and_kfold() -> None:
    assert combinations_attempted_count(5, 2) == 10
    assert exhaustiveness_flag(5) is True
    out = purged_k_fold_cv(_prices(200), k=4, embargo=3)
    assert out["n_folds"] >= 1


def test_attribution_and_ranking() -> None:
    px = _prices(120)
    pos = np.ones_like(px)
    regimes = ["steady_trend"] * 60 + ["high_volatility"] * 59
    by_reg = attribution_by_regime(px, pos, regimes)
    assert "steady_trend" in by_reg
    cond = conditional_performance_attribution({"s": np.diff(px) / px[:-1]}, {"mkt": np.diff(px) / px[:-1]})
    assert "alpha" in cond
    ranked = rank_strategies(
        [
            {"sharpe": 1.0, "max_drawdown_pct": -5, "turnover": 2},
            {"sharpe": 0.2, "max_drawdown_pct": -20, "turnover": 5},
        ]
    )
    assert ranked[0]["rank"] == 1


def test_synthetic_realized_vol_within_30pct() -> None:
    gen = ResearchSyntheticMarketGenerator()
    for key in gen.regime_keys:
        path = gen.generate_path(key, seed=3, length=400)
        target = next(r.vol_annual for r in gen.regimes if r.key == key)
        ratio = float(path["realized_vol"]) / target
        assert 0.7 <= ratio <= 1.3, (key, ratio)
