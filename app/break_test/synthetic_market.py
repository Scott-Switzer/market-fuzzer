from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

import numpy as np


@dataclass(frozen=True)
class GARCHParams:
    omega: float
    alpha: float
    beta: float
    unconditional_vol: float

    @property
    def persistence(self) -> float:
        return float(self.alpha + self.beta)


@dataclass(frozen=True)
class RegimeSpec:
    key: str
    label: str
    mu_annual: float
    vol_annual: float
    kappa: float
    theta_vol: float
    xi: float
    lambda_jump: float
    mu_jump: float
    sigma_jump: float
    jump_reversion_speed: float
    jump_reversion_level: float


@dataclass(frozen=True)
class AssetFactorConfig:
    ticker: str
    company_name: str
    sector: str
    initial_price_ticks: int
    shares_outstanding: int
    initial_fundamental_value_ticks: int
    macro_beta: float
    idiosyncratic_volatility: float
    liquidity_profile: Literal["deep", "normal", "thin"] = "normal"
    event_sensitivity: float = 1.0
    mean_reversion: float = 0.02
    price_cache_factor_loading: float | None = None


@dataclass(frozen=True)
class OneFactorGBMConfig:
    annual_drift: float = 0.06
    annual_volatility: float = 0.18
    correlation_to_market: float = 0.75


FACTOR_NAMES: tuple[str, ...] = (
    "global_equity_market",
    "value",
    "momentum",
    "size",
    "rates",
)

ASSET_FACTOR_ORDER = (
    "SYNTH",
    "BENCH",
    "AUX",
    "S03",
    "S04",
    "S05",
    "S06",
    "S07",
    "S08",
    "S09",
    "S10",
    "S11",
)

FACTOR_LOADINGS = {
    "SYNTH": (0.92, 0.10, 0.15, -0.05, -0.35),
    "BENCH": (0.75, 0.18, 0.20, 0.05, -0.55),
    "AUX": (0.60, 0.22, 0.25, 0.15, -0.45),
    "S03": (0.78, -0.12, 0.30, -0.10, -0.25),
    "S04": (0.68, 0.08, 0.18, 0.20, -0.40),
    "S05": (0.55, 0.25, 0.12, 0.35, -0.20),
    "S06": (0.45, 0.15, 0.35, 0.40, -0.15),
    "S07": (0.35, 0.05, 0.10, 0.50, -0.10),
    "S08": (0.30, -0.08, -0.05, -0.20, 0.75),
    "S09": (0.22, 0.03, 0.05, -0.15, 0.85),
    "S10": (0.18, 0.02, 0.02, -0.25, 0.90),
    "S11": (0.15, 0.01, 0.00, -0.30, 0.95),
}

FACTOR_ANNUAL_VOL = {
    "global_equity_market": 0.16,
    "value": 0.08,
    "momentum": 0.10,
    "size": 0.12,
    "rates": 0.06,
}

FACTOR_CORRELATIONS = np.array(
    [
        [1.00, 0.25, 0.30, 0.35, -0.40],
        [0.25, 1.00, -0.10, 0.15, -0.15],
        [0.30, -0.10, 1.00, 0.05, -0.20],
        [0.35, 0.15, 0.05, 1.00, -0.25],
        [-0.40, -0.15, -0.20, -0.25, 1.00],
    ]
)

# Re-export volume helpers so break-test callers can import from synthetic_market.
from app.exchange.volume_profile import (  # noqa: E402
    displayed_depth_autor,
    flat_intraday_volume_weights,
    intraday_volume_weights,
    u_shaped_intraday_volume_weights,
)


class ResearchSyntheticMarketGenerator:
    def __init__(self, prices: Sequence[float] | None = None) -> None:
        self.prices = np.asarray(prices, dtype=float) if prices is not None else np.array([], dtype=float)
        self.returns = np.diff(np.log(self.prices)) if self.prices.size > 1 else np.array([], dtype=float)
        self.garch_params = self._fit_garch() if self.returns.size > 10 else None
        self.standardized_residuals = self._compute_standardized_residuals() if self.garch_params is not None else None
        self.regimes = self._build_regimes()
        self.regime_labels = {regime.key: regime.label for regime in self.regimes}
        self._regime_labels = dict(self.regime_labels)
        self._factor_covariance_cache = None

    def _fit_garch(self) -> GARCHParams:
        returns = self.returns
        variance = float(np.var(returns, ddof=1))
        if variance <= 1e-20:
            return GARCHParams(omega=1e-8, alpha=0.05, beta=0.90, unconditional_vol=math.sqrt(1e-8 / (1 - 0.95)))
        omega = variance * 0.1
        alpha = 0.06
        beta = 0.92
        for _ in range(100):
            cv = np.empty_like(returns)
            cv[0] = variance
            for idx in range(1, len(returns)):
                cv[idx] = omega + alpha * returns[idx - 1] ** 2 + beta * cv[idx - 1]
            z = returns / np.sqrt(cv)
            step = 1e-6
            omega = max(1e-12, omega + step * np.sum(1.0 / cv) * omega)
            alpha = max(1e-6, min(0.25, alpha + step * np.sum((z**2 - 1.0) * np.roll(returns**2, 1)[1:] / cv[1:]) * alpha))
            beta = max(0.5, min(0.999, beta + step * np.sum((z**2 - 1.0) * np.roll(cv, 1)[1:] / cv[1:]) * beta))
            if abs(omega) < 1e-4 and abs(alpha) < 1e-4 and abs(beta) < 1e-4:
                break
        return GARCHParams(omega=float(omega), alpha=float(alpha), beta=float(beta), unconditional_vol=math.sqrt(omega / max(1.0 - alpha - beta, 1e-8)))

    def _compute_standardized_residuals(self) -> np.ndarray:
        if self.garch_params is None or self.returns.size == 0:
            return np.array([], dtype=float)
        returns = self.returns.astype(float)
        variance = np.empty_like(returns)
        variance[0] = self.garch_params.unconditional_vol ** 2
        for idx in range(1, len(returns)):
            variance[idx] = self.garch_params.omega + self.garch_params.alpha * returns[idx - 1] ** 2 + self.garch_params.beta * variance[idx - 1]
        return returns / np.sqrt(variance)

    def _build_regimes(self) -> list[RegimeSpec]:
        if self.garch_params is not None:
            base_mu = float(np.mean(self.returns) * 252)
            base_vol = float(np.std(self.returns, ddof=1) * math.sqrt(252))
            tail_skewness = float(self._tail_skewness())
        else:
            base_mu = 0.08
            base_vol = 0.15
            tail_skewness = -0.05
        tail_skewness = float(np.clip(tail_skewness, -0.5, 0.5))
        return [
            RegimeSpec(key="steady_trend", label="Steady Trend", mu_annual=base_mu * 0.8, vol_annual=max(base_vol * 0.8, 0.10), kappa=max(base_vol * 0.8 * 4.0, 3.0), theta_vol=max(base_vol * 0.8, 0.10), xi=max(base_vol * 0.8 * 0.25, 0.01), lambda_jump=0.015, mu_jump=0.005, sigma_jump=max(base_vol * 0.8 * 0.15, 0.005), jump_reversion_speed=12.0, jump_reversion_level=0.0),
            RegimeSpec(key="sideways_choppy", label="Sideways & Choppy", mu_annual=base_mu * 0.5, vol_annual=base_vol * 1.1, kappa=max(base_vol * 1.1 * 3.5, 3.0), theta_vol=base_vol * 1.1, xi=max(base_vol * 1.1 * 0.35, 0.01), lambda_jump=0.025, mu_jump=-0.005, sigma_jump=max(base_vol * 1.1 * 0.18, 0.005), jump_reversion_speed=10.0, jump_reversion_level=0.0),
            RegimeSpec(key="high_volatility", label="High Volatility", mu_annual=base_mu * 0.7, vol_annual=base_vol * 1.5, kappa=max(base_vol * 1.5 * 2.5, 3.0), theta_vol=base_vol * 1.5, xi=max(base_vol * 1.5 * 0.45, 0.01), lambda_jump=0.04, mu_jump=-0.015, sigma_jump=max(base_vol * 1.5 * 0.25, 0.005), jump_reversion_speed=6.0, jump_reversion_level=-0.005),
            RegimeSpec(key="sudden_selloff", label="Sudden Selloff", mu_annual=base_mu * 0.5 - 0.05 + tail_skewness * 0.1, vol_annual=base_vol * 2.5, kappa=max(base_vol * 2.5 * 2.0, 2.0), theta_vol=base_vol * 2.5, xi=max(base_vol * 2.5 * 0.6, 0.01), lambda_jump=0.07, mu_jump=-0.035, sigma_jump=max(base_vol * 2.5 * 0.35, 0.01), jump_reversion_speed=3.0, jump_reversion_level=-0.012),
        ]

    def _tail_skewness(self, percentile: float = 5.0) -> float:
        returns = self.returns
        if returns.size == 0:
            return 0.0
        mean = float(np.mean(returns))
        std = float(np.std(returns, ddof=1))
        if std < 1e-20:
            return 0.0
        lower = float(np.percentile(returns, percentile))
        upper = float(np.percentile(returns, 100.0 - percentile))
        denom = upper - mean
        if abs(denom) < 1e-20:
            return 0.0
        return float((lower - mean) / std / denom)

    @property
    def regime_keys(self) -> tuple[str, ...]:
        return tuple(r.key for r in self.regimes)

    def detect_regime(self, prices: Sequence[float]) -> dict[str, object]:
        px = np.asarray(prices, dtype=float)
        if px.size < 5:
            return {"regime": "mixed", "detected_drift": 0.0, "detected_volatility": 0.0, "high_vol_periods_pct": 0.0, "tail_skewness": 0.0, "length": int(px.size)}
        log_returns = np.diff(np.log(px))
        vol = float(np.std(log_returns, ddof=1)) * math.sqrt(252) if log_returns.size > 1 else 0.0
        avg_return = float(np.mean(log_returns) * 252) if log_returns.size else 0.0
        if len(log_returns) >= 20:
            rolling_var = np.convolve(log_returns ** 2, np.ones(20) / 20, mode="valid")
            rolling_std = np.sqrt(np.maximum(rolling_var, 1e-20))
            median_std = float(np.median(rolling_std))
            high_vol_periods = float(np.mean(rolling_std > 1.5 * median_std)) * 100 if median_std > 1e-12 else 0.0
        else:
            high_vol_periods = 0.0
        if vol < 0.12:
            regime = "low-vol / likely trend or range"
        elif vol < 0.22:
            regime = "normal-vol / mixed"
        elif vol < 0.35:
            regime = "elevated-vol / stress risk"
        else:
            regime = "crisis-vol / tail risk"
        return {
            "regime": regime,
            "detected_drift": round(avg_return * 100, 2),
            "detected_volatility": round(vol * 100, 2),
            "high_vol_periods_pct": round(high_vol_periods, 1),
            "length": int(px.size),
        }

    def detect_regimes(self, prices: Sequence[float]) -> dict[str, object]:
        return self.detect_regime(prices)

    def generate_path(self, regime_key: str, seed: int, length: int = 120, base_price: float = 100.0, target_asset: str = "SYNTH") -> dict[str, object]:
        rng = np.random.default_rng(seed % (2**31))
        returns = rng.normal(0.0, 0.015, length - 1)
        prices_arr = np.empty(length, dtype=float)
        prices_arr[0] = float(base_price)
        prices_arr[1:] = prices_arr[0] * np.exp(np.cumsum(returns))
        return {
            "target_asset": target_asset,
            "regime": regime_key,
            "seed": seed,
            "prices": [round(float(v), 6) for v in prices_arr.tolist()],
            "returns": [round(float(v), 6) for v in returns.tolist()],
        }

    def generate_correlated_gbm_paths(
        self,
        regime_key: str,
        seed: int,
        asset_tickers: Sequence[str],
        base_prices: Sequence[float],
        length: int = 120,
        annual_factor_vols: dict[str, float] | None = None,
        factor_correlations: np.ndarray | None = None,
        idiosyncratic_vol_shrink: float = 0.02,
    ) -> dict[str, dict[str, object]]:
        if len(asset_tickers) != len(base_prices):
            raise ValueError("asset_tickers and base_prices must have the same length")
        regime = next((r for r in self.regimes if r.key == regime_key), self.regimes[1] if self.regimes else None)
        if regime is None:
            return {ticker: self.generate_path(regime_key, seed + idx, length=length, base_price=float(base_prices[idx]), target_asset=ticker) for idx, ticker in enumerate(asset_tickers)}
        covariance = self._build_asset_factor_covariance(
            asset_tickers,
            factor_correlations=factor_correlations,
            annual_factor_vols=annual_factor_vols,
            idiosyncratic_vol_shrink=idiosyncratic_vol_shrink,
        )
        cholesky_lower = None
        num_assets = len(asset_tickers)
        if num_assets > 1:
            covariance = covariance + np.eye(num_assets) * 1e-12
            try:
                cholesky_lower = np.linalg.cholesky(covariance)
            except np.linalg.LinAlgError:
                eigenvals, eigenvecs = np.linalg.eigh(covariance)
                eigenvals = np.clip(eigenvals, 1e-12, None)
                covariance = eigenvecs @ np.diag(eigenvals) @ eigenvecs.T
                cholesky_lower = np.linalg.cholesky((covariance + covariance.T) / 2.0)
        steps = max(10, int(length))
        result: dict[str, dict[str, object]] = {}
        for idx, ticker in enumerate(asset_tickers):
            rng = np.random.default_rng((seed + idx * 1237) % (2**32 - 1))
            base_price = float(base_prices[idx])
            base_price = 100.0 if base_price <= 0 else base_price
            if num_assets == 1:
                shocks = rng.standard_normal(steps - 1)
            else:
                shocks = rng.standard_normal((num_assets, steps - 1))
                shocks = cholesky_lower @ shocks
                shocks = shocks[idx]
            log_prices = np.empty(steps, dtype=float)
            log_prices[0] = math.log(base_price)
            daily_vol_sq = float(covariance[idx, idx])
            vol_sqrt_annual = math.sqrt(max(daily_vol_sq, 1e-20))
            for step in range(1, steps):
                eps = float(shocks[step - 1])
                d_log_price = (regime.mu_annual - 0.5 * daily_vol_sq) * (1.0 / 252.0) + vol_sqrt_annual * math.sqrt(1.0 / 252.0) * eps
                log_prices[step] = log_prices[step - 1] + d_log_price
            prices_arr = np.exp(log_prices)
            result[ticker] = {
                "target_asset": ticker,
                "regime": regime.key,
                "seed": seed,
                "prices": [round(float(p), 6) for p in prices_arr.tolist()],
                "returns": [round(float(r), 6) for r in np.diff(log_prices).tolist()],
                "covariance_method": "factor_cholesky",
                "factor_loadings": tuple(),
                "annual_factor_vols": {},
                "use_price_cache": False,
            }
        return result

    def _build_asset_factor_covariance(
        self,
        asset_tickers: Sequence[str],
        factor_correlations: np.ndarray | None = None,
        annual_factor_vols: dict[str, float] | None = None,
        idiosyncratic_vol_shrink: float = 0.02,
    ) -> np.ndarray:
        if factor_correlations is None:
            factor_correlations = FACTOR_CORRELATIONS
        if annual_factor_vols is None:
            annual_factor_vols = FACTOR_ANNUAL_VOL
        num_assets = len(asset_tickers)
        if num_assets == 0:
            return np.ones((0, 0), dtype=float)
        if num_assets == 1:
            return np.ones((1, 1), dtype=float)
        regression_matrix = np.empty((num_assets, len(FACTOR_NAMES)), dtype=float)
        for row_idx, ticker in enumerate(asset_tickers):
            macro_beta = {"SYNTH": 1.0, "BENCH": 0.5, "AUX": 0.3}.get(ticker, max(0.15, 1.0 - 0.07 * row_idx))
            exposures = [macro_beta if factor == "global_equity_market" else 0.0 for factor in FACTOR_NAMES]
            regression_matrix[row_idx] = exposures
        factor_vols = np.array([annual_factor_vols[factor] for factor in FACTOR_NAMES], dtype=float)
        factor_cov = np.outer(factor_vols, factor_vols) * factor_correlations
        systematic_cov = regression_matrix @ factor_cov @ regression_matrix.T
        idiosyncratic_var = np.zeros(num_assets, dtype=float)
        for idx in range(num_assets):
            ticker = asset_tickers[idx]
            if ticker == "SYNTH":
                base_idios = 0.002 ** 2
            elif ticker == "BENCH":
                base_idios = 0.001 ** 2
            elif ticker == "AUX":
                base_idios = 0.0015 ** 2
            else:
                base_idios = (0.001 + 0.0002 * idx) ** 2
            idiosyncratic_var[idx] = base_idios + idiosyncratic_vol_shrink ** 2
        covariance = systematic_cov + np.diag(idiosyncratic_var)
        covariance = (covariance + covariance.T) / 2.0
        return covariance

    def _build_regime_specs(self) -> dict[str, dict[str, float]]:
        specs = {}
        for regime in self.regimes:
            specs[regime.key] = {"mu_annual": regime.mu_annual, "vol_annual": regime.vol_annual, "kappa": regime.kappa, "theta_vol": regime.theta_vol, "xi": regime.xi, "lambda_jump": regime.lambda_jump, "mu_jump": regime.mu_jump, "sigma_jump": regime.sigma_jump, "jump_reversion_speed": regime.jump_reversion_speed, "jump_reversion_level": regime.jump_reversion_level}
        return specs

    def draw_standardized_residuals(self, size: int, rng: np.random.Generator, ar_persistence: float = 0.15) -> np.ndarray:
        if self.standardized_residuals is None or self.standardized_residuals.size < 4:
            return rng.standard_normal(size)
        residuals = np.asarray(self.standardized_residuals, dtype=float)
        draws = rng.choice(residuals, size=size, replace=True)
        if ar_persistence != 0.0 and size > 1:
            ar_noise = rng.standard_normal(size)
            for idx in range(1, size):
                draws[idx] += ar_persistence * draws[idx - 1] + 0.1 * ar_noise[idx]
        clip_limit = float(np.percentile(np.abs(residuals), 99.5))
        return np.clip(draws, -clip_limit, clip_limit)
