from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from app.break_test.exchange_fwd import (
    DEFAULT_ASSETS,
    EXPANDED_UNIVERSE_PRESETS,
    _build_correlated_synthetic_paths,
    _build_one_factor_paths_for_assets,
)
from app.break_test.metrics import backtest_metrics
from app.break_test.strategies import compute_positions
from app.break_test.synthetic_market import (
    AssetFactorConfig,
    OneFactorGBMConfig,
)


def _resolve_asset_universe(
    asset_count: int = 3,
    universe_preset: str | None = None,
    universe_csv: str | Path | None = None,
    strategy_asset: str = "SYNTH",
) -> tuple[tuple[AssetFactorConfig, ...], str]:
    if asset_count < 1:
        raise ValueError("asset_count must be >= 1")
    if universe_csv:
        source = Path(universe_csv)
        if not source.exists():
            raise FileNotFoundError(f"Universe CSV not found: {universe_csv}")
        asset_lines = source.read_text(encoding="utf-8").splitlines()
        asset_lines = [
            line.strip() for line in asset_lines if line.strip() and not line.strip().startswith("#")
        ]
        if not asset_lines:
            raise ValueError(f"No asset definitions found in {universe_csv}")
        asset_specs = [_parse_universe_csv_row(line) for line in asset_lines[1 : asset_count + 1]]
        if not any(asset.ticker == strategy_asset for asset in asset_specs):
            asset_specs[0] = AssetFactorConfig(
                ticker=strategy_asset,
                company_name="Strategy Asset",
                sector="Strategy",
                initial_price_ticks=10_000,
                shares_outstanding=50_000_000,
                initial_fundamental_value_ticks=10_000,
                macro_beta=1.0,
                idiosyncratic_volatility=0.002,
                liquidity_profile="deep",
                event_sensitivity=1.0,
                mean_reversion=0.02,
                price_cache_factor_loading=1.0,
            )
        while len(asset_specs) < asset_count:
            asset_specs.append(
                AssetFactorConfig(
                    ticker=f"SYNTH_{len(asset_specs):02d}",
                    company_name=f"Synthetic {len(asset_specs):02d}",
                    sector="Synthetic",
                    initial_price_ticks=max(5_000, 14_000 - len(asset_specs) * 1_250),
                    shares_outstanding=max(10_000_000, 120_000_000 - len(asset_specs) * 10_000_000),
                    initial_fundamental_value_ticks=max(5_000, 14_000 - len(asset_specs) * 1_250),
                    macro_beta=round(1.0 - 0.07 * len(asset_specs), 6),
                    idiosyncratic_volatility=round(0.001 + 0.0002 * len(asset_specs), 6),
                    liquidity_profile="normal",
                    event_sensitivity=round(1.0 - 0.04 * len(asset_specs), 6),
                    mean_reversion=round(0.02 + 0.004 * len(asset_specs), 6),
                    price_cache_factor_loading=round(0.88 - 0.04 * len(asset_specs), 6),
                )
            )
        return tuple(asset_specs[:asset_count]), strategy_asset

    if universe_preset and universe_preset in EXPANDED_UNIVERSE_PRESETS:
        asset_specs = list(EXPANDED_UNIVERSE_PRESETS[universe_preset][:asset_count])
        if not any(asset.ticker == strategy_asset for asset in asset_specs):
            asset_specs[0] = AssetFactorConfig(
                ticker=strategy_asset,
                company_name="Strategy Asset",
                sector="Strategy",
                initial_price_ticks=10_000,
                shares_outstanding=50_000_000,
                initial_fundamental_value_ticks=10_000,
                macro_beta=1.0,
                idiosyncratic_volatility=0.002,
                liquidity_profile="deep",
                event_sensitivity=1.0,
                mean_reversion=0.02,
                price_cache_factor_loading=1.0,
            )
        return tuple(asset_specs[:asset_count]), strategy_asset

    if asset_count <= len(DEFAULT_ASSETS):
        return DEFAULT_ASSETS[:asset_count], strategy_asset
    asset_specs = list(DEFAULT_ASSETS)
    for index in range(len(DEFAULT_ASSETS), asset_count):
        asset_specs.append(
            AssetFactorConfig(
                ticker=f"SYNTH_{index:02d}",
                company_name=f"Synthetic {index:02d}",
                sector="Synthetic",
                initial_price_ticks=max(5_000, 14_000 - index * 1_250),
                shares_outstanding=max(10_000_000, 120_000_000 - index * 10_000_000),
                initial_fundamental_value_ticks=max(5_000, 14_000 - index * 1_250),
                macro_beta=round(1.0 - 0.07 * index, 6),
                idiosyncratic_volatility=round(0.001 + 0.0002 * index, 6),
                liquidity_profile="normal",
                event_sensitivity=round(1.0 - 0.04 * index, 6),
                mean_reversion=round(0.02 + 0.004 * index, 6),
                price_cache_factor_loading=round(0.88 - 0.04 * index, 6),
            )
        )
    return tuple(asset_specs[:asset_count]), strategy_asset


def _parse_universe_csv_row(line: str) -> AssetFactorConfig:
    fields = [field.strip() for field in line.replace("\t", ",").split(",") if field.strip()]
    if len(fields) < 9:
        raise ValueError(f"Universe CSV row requires at least 9 fields; got {fields!r}")
    (
        ticker,
        company_name,
        sector,
        price_tick_str,
        shares_str,
        fv_str,
        beta_str,
        idios_vol_str,
        liquidity_profile,
    ) = fields[:9]
    return AssetFactorConfig(
        ticker=ticker.upper(),
        company_name=company_name,
        sector=sector,
        initial_price_ticks=int(float(price_tick_str)),
        shares_outstanding=int(float(shares_str)),
        initial_fundamental_value_ticks=int(float(fv_str)),
        macro_beta=float(beta_str),
        idiosyncratic_volatility=float(idios_vol_str),
        liquidity_profile=liquidity_profile,
        event_sensitivity=float(fields[9]) if len(fields) > 9 else 1.0,
        mean_reversion=float(fields[10]) if len(fields) > 10 else 0.02,
        price_cache_factor_loading=float(fields[11]) if len(fields) > 11 else None,
    )


def build_multi_asset_forward_results(
    closes: list[float],
    strategy_type: str,
    params: dict[str, int],
    asset_count: int = 3,
    universe_preset: str | None = None,
    universe_csv: str | Path | None = None,
    worlds_per_regime: int = 10,
    price_cache: dict[str, Sequence[float]] | None = None,
    prices_cache: dict[str, Sequence[float]] | None = None,
    use_price_cache: bool = False,
    generate_one_factor_paths: bool = False,
    one_factor_gbm_config: OneFactorGBMConfig | None = None,
) -> dict[str, object]:
    prices = np.asarray(closes, dtype=float)
    resolved_assets, target_asset = _resolve_asset_universe(
        asset_count=asset_count,
        universe_preset=universe_preset,
        universe_csv=universe_csv,
        strategy_asset="SYNTH",
    )
    length = len(prices)
    regime_keys = ["steady_trend", "sideways_choppy", "high_volatility", "sudden_selloff"]
    regime_results: dict[str, list[float]] = {key: [] for key in regime_keys}
    regime_drawdowns: dict[str, list[float]] = {key: [] for key in regime_keys}
    regime_world_counts: dict[str, int] = {key: 0 for key in regime_keys}

    for regime_index, regime_key in enumerate(regime_keys):
        for world_idx in range(worlds_per_regime):
            seed = 40_000 + regime_index * 1_000 + world_idx
            if generate_one_factor_paths:
                paths = _build_one_factor_paths_for_assets(
                    assets=resolved_assets,
                    regime_key=regime_key,
                    seed=seed,
                    length=length,
                    price_cache=price_cache,
                    prices_cache=prices_cache,
                    use_price_cache=use_price_cache,
                    gbm_config=one_factor_gbm_config,
                )
            else:
                paths = _build_correlated_synthetic_paths(
                    assets=resolved_assets,
                    regime_key=regime_key,
                    seed=seed,
                    length=length,
                    price_cache=price_cache,
                    prices_cache=prices_cache,
                    use_price_cache=use_price_cache,
                )
            syn_prices = np.array(paths.get(target_asset, {}).get("prices", []), dtype=float)
            if syn_prices.size < max(
                10,
                params.get("slow", 50)
                if strategy_type == "sma_crossover"
                else params.get("entry_lookback", 20),
            ):
                continue
            syn_positions = compute_positions(strategy_type, syn_prices, **params)
            metrics = backtest_metrics(syn_prices, syn_positions)
            regime_results[regime_key].append(float(metrics["total_return_pct"]))
            regime_drawdowns[regime_key].append(float(metrics["max_drawdown_pct"]))
            regime_world_counts[regime_key] += 1

    summary = []
    for regime_key in regime_keys:
        returns = regime_results[regime_key]
        drawdowns = regime_drawdowns[regime_key]
        if not returns:
            continue
        losses = sum(1 for value in returns if value < 0)
        summary.append(
            {
                "regime": regime_key.replace("_", " ").title(),
                "regime_key": regime_key,
                "worlds": regime_world_counts[regime_key],
                "loss_rate_pct": round(losses / len(returns) * 100, 1),
                "median_return_pct": round(float(np.median(returns)), 2),
                "mean_return_pct": round(float(np.mean(returns)), 2),
                "worst_drawdown_pct": round(float(np.min(drawdowns)), 2) if drawdowns else 0.0,
                "best_return_pct": round(float(np.max(returns)), 2),
            }
        )
    return {
        "strategy": {"type": strategy_type, "parameters": params},
        "asset_count": asset_count,
        "universe_preset": universe_preset,
        "universe_csv": str(universe_csv) if universe_csv else None,
        "target_asset": target_asset,
        "generate_one_factor_paths": generate_one_factor_paths,
        "forward_results": summary,
    }
