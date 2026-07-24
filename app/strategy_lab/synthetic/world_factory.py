from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.break_test.costs import TransactionCostModel
from app.break_test.metrics import (
    _borrow_fee_bps,
    _impact_bps,
    _spread_bps,
    _tiered_fee_bps,
    backtest_metrics,
    toxicity_bps,
)
from app.break_test.synthetic_market import (
    FACTOR_ANNUAL_VOL,
    FACTOR_CORRELATIONS,
    ResearchSyntheticMarketGenerator,
)
from app.strategy_lab.synthetic.asset_anonymizer import AssetAnonymizer


def _coerce_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _volatility_regime_key(realized_annual_vol: float) -> str:
    if realized_annual_vol < 0.12:
        return "steady_trend"
    if realized_annual_vol < 0.22:
        return "sideways_choppy"
    if realized_annual_vol < 0.35:
        return "high_volatility"
    return "sudden_selloff"


def _seed_for_asset(base_seed: int, index: int) -> int:
    return int(base_seed + 17 * index + 7) % (2**32 - 1)


class WorldFactory:
    @staticmethod
    def create(manifest: dict[str, Any], seed: int) -> dict[str, Any]:
        assets = list(manifest.get("assets") or [])
        if not assets:
            assets = ["SYNTH", "BENCH", "AUX"]

        real_tickers = [str(asset.get("ticker", f"ASSET_{idx:02d}")) for idx, asset in enumerate(assets)]
        base_prices = [_coerce_float(asset.get("base_price", 100.0), 100.0) for asset in assets]
        steps = int(manifest.get("steps") or 120)
        regime = str(manifest.get("regime") or "steady_trend")
        fee_schedule = manifest.get("fee_schedule")
        locate_annual_bps = _coerce_float(manifest.get("locate_annual_bps"), 200.0)
        htb_annual_bps = _coerce_float(manifest.get("htb_annual_bps"), 0.0)
        toxicity_kappa = _coerce_float(manifest.get("toxicity_kappa"), 5.0)
        default_adv = _coerce_float(manifest.get("default_adv"), 1_000_000.0)
        adtv_scaled = manifest.get("adtv_scaled")
        failure_threshold = _coerce_float(manifest.get("failure_completion_threshold"), 0.80)

        anonymized_tickers = AssetAnonymizer.anonymize(real_tickers)
        ticker_map = dict(zip(real_tickers, anonymized_tickers, strict=True))

        generator = ResearchSyntheticMarketGenerator()
        asset_paths = generator.generate_correlated_gbm_paths(
            regime_key=regime,
            seed=int(seed),
            asset_tickers=list(real_tickers),
            base_prices=base_prices,
            length=steps,
            annual_factor_vols=FACTOR_ANNUAL_VOL,
            factor_correlations=FACTOR_CORRELATIONS,
        )

        evaluated_assets: dict[str, dict[str, Any]] = {}
        returning_count = 0
        worst_margin = 0.0
        failure_details: dict[str, Any] = {}

        for idx, ticker in enumerate(real_tickers):
            anon_ticker = ticker_map[ticker]
            path: dict[str, Any] = asset_paths.get(ticker) or asset_paths.get(anon_ticker) or {}  # type: ignore[assignment]
            prices = [float(px) for px in (path.get("prices") or [])]
            returns = [float(rt) for rt in (path.get("returns") or [])]
            if len(prices) < 2:
                prices = [float(base_prices[idx])] * max(steps, 2)
                returns = [0.0] * max(steps - 1, 1)

            prices_arr = np.asarray(prices, dtype=float)
            returns_arr = np.asarray(returns, dtype=float)
            positions_arr = np.ones_like(prices_arr)
            trade_qty = np.diff(positions_arr, prepend=positions_arr[0])
            signed_flow_prev = float(np.nan_to_num(np.mean(returns_arr[:5]), nan=0.0))
            depth_prev = float(np.nan_to_num(np.mean(prices_arr[:5]), nan=0.0)) * 0.1

            realized_annual_vol = (
                float(np.std(returns_arr, ddof=1) * math.sqrt(252.0)) if returns_arr.size > 1 else 0.0
            )
            regime_key = _volatility_regime_key(realized_annual_vol)
            adtv_series = (
                np.asarray(adtv_scaled, dtype=float)
                if adtv_scaled is not None
                else np.full(prices_arr.size, default_adv, dtype=float)
            )
            if adtv_series.size != prices_arr.size:
                adtv_series = np.full(prices_arr.size, float(np.mean(adtv_series)), dtype=float)

            exchange_spec = _FakeExchangeSpec(
                adtv=float(adtv_series[0]) if adtv_series.size else default_adv,
                fee_schedule=fee_schedule,
                locate_fee_bps_annual=locate_annual_bps,
                htb_bps_annual=htb_annual_bps,
                toxicity_kappa=toxicity_kappa,
                taker_fee_bps=0.3,
            )

            prices_metric = prices_arr
            positions_metric = positions_arr
            if prices_metric.size > positions_metric.size:
                prices_metric = prices_metric[: positions_metric.size]
            elif positions_metric.size > prices_metric.size:
                positions_metric = positions_metric[: positions_metric.size]

            signed_flow_arr = np.full(prices_metric.size - 1, signed_flow_prev, dtype=float)
            depth_arr = np.full(prices_metric.size - 1, depth_prev, dtype=float)
            metrics = backtest_metrics(
                prices=prices_metric,
                positions=positions_metric,
                exchange_spec=exchange_spec,
                tcost_model=TransactionCostModel(
                    spread_bps=2.0,
                    impact_mode="almgren_chriss",
                    default_adv=default_adv,
                    locate_fee_bps_annual=locate_annual_bps,
                    htb_bps_annual=htb_annual_bps,
                    toxicity_kappa=toxicity_kappa,
                ),
                default_adv=default_adv,
                signed_flow=signed_flow_arr.tolist(),
                depth=depth_arr.tolist(),
                side="buy",
                arrival_price=float(prices_metric[0]),
                average_execution_price=float(np.mean(prices_metric)),
                market_vwap=float(np.mean(prices_metric)),
                filled_quantity=float(np.sum(np.abs(trade_qty))),
                target_quantity=float(np.sum(np.abs(trade_qty))),
            )
            costs_bps = float(
                np.mean(
                    exchange_spec.tcost_model.costs_for_signals(
                        prices_metric,
                        positions_metric,
                        default_adv=default_adv,
                        signed_flow=signed_flow_arr.tolist(),
                        depth=depth_arr.tolist(),
                    )
                )
            )
            daily_vol = float(np.std(returns_arr, ddof=1)) if returns_arr.size > 1 else 0.015
            spread = _spread_bps(daily_vol)
            perm_bps, temp_bps = _impact_bps(0.01, daily_vol)
            fee_bps = _tiered_fee_bps(
                int(round(default_adv * float(np.mean(prices_metric)) * 100)),
                fee_schedule,
                0.3,
            )
            borrow_bps = _borrow_fee_bps(
                short_position=0.0,
                price=float(np.mean(prices_metric)),
                adtv=default_adv,
                locate_annual_bps=locate_annual_bps,
                htb_annual_bps=htb_annual_bps,
            )
            tox = toxicity_bps(signed_flow_prev, depth_prev, kappa=toxicity_kappa)
            total_slippage_bps = spread + perm_bps + temp_bps + fee_bps + borrow_bps + tox
            completion = _coerce_float(metrics.get("fill_rate", 1.0), 1.0)
            if completion >= failure_threshold:
                returning_count += 1

            evaluated_assets[anon_ticker] = {
                "real_ticker": ticker,
                "regime": regime_key,
                "seed": _seed_for_asset(seed, idx),
                "prices": [round(float(v), 6) for v in prices],
                "returns": [round(float(v), 6) for v in returns],
                "backtest_metrics": metrics,
                "mean_cost_bps": round(costs_bps, 4),
                "slippage_bps": round(total_slippage_bps, 4),
                "completion_pct": round(completion, 6),
                "realized_annual_vol": round(realized_annual_vol, 6),
                "avg_correlation": round(float(path.get("avg_correlation") or 0.0), 6),
                "jump_count": int(path.get("jump_count") or 0),
                "delisted": bool(path.get("delisted") or False),
                "failure": completion < failure_threshold,
                "margin": round(completion - failure_threshold, 6),
            }
            if evaluated_assets[anon_ticker]["failure"]:
                failure_details[anon_ticker] = evaluated_assets[anon_ticker]
            worst_margin = min(worst_margin, evaluated_assets[anon_ticker]["margin"])

        aggregate_returns = []
        for ticker in anonymized_tickers:
            aggregate_returns.extend(evaluated_assets[ticker]["returns"])
        returns_arr = np.asarray(aggregate_returns, dtype=float)
        if returns_arr.size > 1:
            realized_annual_vol = float(np.std(returns_arr, ddof=1) * math.sqrt(252.0))
        else:
            realized_annual_vol = 0.0

        return {
            "status": "generated",
            "manifest": manifest,
            "seed": int(seed),
            "regime": regime,
            "anonymized_assets": evaluated_assets,
            "ticker_map": ticker_map,
            "returning_count": returning_count,
            "failure_count": len(anonymized_tickers) - returning_count,
            "worst_property_margin": round(worst_margin, 6),
            "first_failure_scenario": {
                "scenario": {"regime": regime, "seed": int(seed), "failure_count": len(failure_details)},
                "margin": round(worst_margin, 6),
                "failure": bool(failure_details),
                "assets": failure_details,
            },
            "world_metrics": {
                "realized_annual_vol": round(realized_annual_vol, 6),
                "asset_count": len(anonymized_tickers),
                "pairs_with_prices": len([t for t in anonymized_tickers if evaluated_assets[t]["prices"]]),
            },
        }


class _FakeExchangeSpec:
    def __init__(
        self,
        adtv: float = 1_000_000.0,
        fee_schedule: Any = None,
        locate_fee_bps_annual: float = 200.0,
        htb_bps_annual: float = 0.0,
        toxicity_kappa: float = 5.0,
        taker_fee_bps: float = 0.3,
    ) -> None:
        self.adtv = float(adtv)
        self.fee_schedule = fee_schedule
        self.locate_fee_bps_annual = float(locate_fee_bps_annual)
        self.htb_bps_annual = float(htb_bps_annual)
        self.toxicity_kappa = float(toxicity_kappa)
        self.taker_fee_bps = float(taker_fee_bps)
        self.tcost_model = TransactionCostModel(
            spread_bps=max(2.0, taker_fee_bps * 2.0),
            impact_mode="almgren_chriss",
            default_adv=self.adtv,
            locate_fee_bps_annual=self.locate_fee_bps_annual,
            htb_bps_annual=self.htb_bps_annual,
            toxicity_kappa=self.toxicity_kappa,
        )
