from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from app.break_test.metrics import backtest_metrics
from app.break_test.synthetic_market import (
    AssetFactorConfig,
    OneFactorGBMConfig,
    ResearchSyntheticMarketGenerator,
)
from app.schemas import (
    AgentPopulation,
    AssetSpec,
    AgentsSpec,
    ClockSpec,
    ExchangeSpec,
    ExperimentSpec,
    InterventionSpec,
    MacroSpec,
    ParentOrderSpec,
    WorldSpec,
)

try:
    from app.simulation import run_simulation
except ImportError:  # pragma: no cover
    def run_simulation(*args, **kwargs):
        raise RuntimeError("app.simulation is unavailable from exchange_fwd.py")


# Default sealed forward-test engine (EventKernelV2 provenance + V1 CLOB matching).
DEFAULT_EXCHANGE_ENGINE = "v2"

VOLATILITY_LABELS = ["low", "normal", "elevated", "crisis"]

REGIME_CONFIGS: dict[str, dict[str, float]] = {
    "steady_trend": {
        "vol_mult": 0.7,
        "vol_label_idx": 1,
        "latency": "normal",
        "depth": 600,
    },
    "sideways_choppy": {
        "vol_mult": 1.25,
        "vol_label_idx": 2,
        "latency": "normal",
        "depth": 400,
    },
    "high_volatility": {
        "vol_mult": 2.2,
        "vol_label_idx": 3,
        "latency": "high",
        "depth": 300,
    },
    "sudden_selloff": {
        "vol_mult": 1.8,
        "vol_label_idx": 3,
        "latency": "high",
        "depth": 400,
    },
}

DEFAULT_ASSETS: tuple[AssetFactorConfig, ...] = (
    AssetFactorConfig(
        ticker="SYNTH",
        company_name="Synthetic Asset",
        sector="Synthetic",
        initial_price_ticks=10_000,
        shares_outstanding=50_000_000,
        initial_fundamental_value_ticks=10_000,
        macro_beta=1.0,
        idiosyncratic_volatility=0.002,
        liquidity_profile="normal",
        event_sensitivity=1.0,
        mean_reversion=0.02,
        price_cache_factor_loading=1.0,
    ),
    AssetFactorConfig(
        ticker="BENCH",
        company_name="Benchmark",
        sector="Synthetic",
        initial_price_ticks=10_000,
        shares_outstanding=50_000_000,
        initial_fundamental_value_ticks=10_000,
        macro_beta=0.5,
        idiosyncratic_volatility=0.001,
        liquidity_profile="deep",
        event_sensitivity=0.9,
        mean_reversion=0.03,
        price_cache_factor_loading=0.6,
    ),
    AssetFactorConfig(
        ticker="AUX",
        company_name="Auxiliary",
        sector="Synthetic",
        initial_price_ticks=5_000,
        shares_outstanding=100_000_000,
        initial_fundamental_value_ticks=5_000,
        macro_beta=0.3,
        idiosyncratic_volatility=0.0015,
        liquidity_profile="normal",
        event_sensitivity=0.8,
        mean_reversion=0.04,
        price_cache_factor_loading=0.45,
    ),
)

EIGHT_ASSET_UNIVERSE = tuple(list(DEFAULT_ASSETS) + [
    AssetFactorConfig(ticker="XLK", company_name="Technology Select Sector", sector="Technology",
                      initial_price_ticks=18_000, shares_outstanding=80_000_000,
                      initial_fundamental_value_ticks=18_000, macro_beta=0.78, idiosyncratic_volatility=0.0018,
                      liquidity_profile="deep", event_sensitivity=1.05, mean_reversion=0.018, price_cache_factor_loading=0.76),
    AssetFactorConfig(ticker="XLF", company_name="Financial Select Sector", sector="Financials",
                      initial_price_ticks=16_500, shares_outstanding=70_000_000,
                      initial_fundamental_value_ticks=16_500, macro_beta=0.68, idiosyncratic_volatility=0.0016,
                      liquidity_profile="deep", event_sensitivity=0.98, mean_reversion=0.021, price_cache_factor_loading=0.70),
    AssetFactorConfig(ticker="XLE", company_name="Energy Select Sector", sector="Energy",
                      initial_price_ticks=15_200, shares_outstanding=40_000_000,
                      initial_fundamental_value_ticks=15_200, macro_beta=0.58, idiosyncratic_volatility=0.0022,
                      liquidity_profile="normal", event_sensitivity=1.15, mean_reversion=0.025, price_cache_factor_loading=0.62),
    AssetFactorConfig(ticker="RATES", company_name="Rates Proxy", sector="Macro/Rates",
                      initial_price_ticks=12_000, shares_outstanding=100_000_000,
                      initial_fundamental_value_ticks=12_000, macro_beta=0.22, idiosyncratic_volatility=0.0012,
                      liquidity_profile="deep", event_sensitivity=0.85, mean_reversion=0.03, price_cache_factor_loading=0.35),
    AssetFactorConfig(ticker="FX", company_name="FX Proxy", sector="Macro/FX",
                      initial_price_ticks=11_500, shares_outstanding=90_000_000,
                      initial_fundamental_value_ticks=11_500, macro_beta=0.18, idiosyncratic_volatility=0.0013,
                      liquidity_profile="normal", event_sensitivity=0.80, mean_reversion=0.032, price_cache_factor_loading=0.32),
])

TWELVE_ASSET_UNIVERSE = tuple(list(DEFAULT_ASSETS) + [
    AssetFactorConfig(ticker="XLK", company_name="Technology Select Sector", sector="Technology",
                      initial_price_ticks=18_000, shares_outstanding=80_000_000,
                      initial_fundamental_value_ticks=18_000, macro_beta=0.78, idiosyncratic_volatility=0.0018,
                      liquidity_profile="deep", event_sensitivity=1.05, mean_reversion=0.018, price_cache_factor_loading=0.76),
    AssetFactorConfig(ticker="XLF", company_name="Financial Select Sector", sector="Financials",
                      initial_price_ticks=16_500, shares_outstanding=70_000_000,
                      initial_fundamental_value_ticks=16_500, macro_beta=0.68, idiosyncratic_volatility=0.0016,
                      liquidity_profile="deep", event_sensitivity=0.98, mean_reversion=0.021, price_cache_factor_loading=0.70),
    AssetFactorConfig(ticker="XLE", company_name="Energy Select Sector", sector="Energy",
                      initial_price_ticks=15_200, shares_outstanding=40_000_000,
                      initial_fundamental_value_ticks=15_200, macro_beta=0.58, idiosyncratic_volatility=0.0022,
                      liquidity_profile="normal", event_sensitivity=1.15, mean_reversion=0.025, price_cache_factor_loading=0.62),
    AssetFactorConfig(ticker="RATES", company_name="Rates Proxy", sector="Macro/Rates",
                      initial_price_ticks=12_000, shares_outstanding=100_000_000,
                      initial_fundamental_value_ticks=12_000, macro_beta=0.22, idiosyncratic_volatility=0.0012,
                      liquidity_profile="deep", event_sensitivity=0.85, mean_reversion=0.03, price_cache_factor_loading=0.35),
    AssetFactorConfig(ticker="FX", company_name="FX Proxy", sector="Macro/FX",
                      initial_price_ticks=11_500, shares_outstanding=90_000_000,
                      initial_fundamental_value_ticks=11_500, macro_beta=0.18, idiosyncratic_volatility=0.0013,
                      liquidity_profile="normal", event_sensitivity=0.80, mean_reversion=0.032, price_cache_factor_loading=0.32),
    AssetFactorConfig(ticker="XLV", company_name="Health Care Select Sector", sector="Health Care",
                      initial_price_ticks=17_000, shares_outstanding=55_000_000,
                      initial_fundamental_value_ticks=17_000, macro_beta=0.65, idiosyncratic_volatility=0.0017,
                      liquidity_profile="deep", event_sensitivity=1.00, mean_reversion=0.019, price_cache_factor_loading=0.68),
    AssetFactorConfig(ticker="XLI", company_name="Industrial Select Sector", sector="Industrials",
                      initial_price_ticks=14_800, shares_outstanding=45_000_000,
                      initial_fundamental_value_ticks=14_800, macro_beta=0.72, idiosyncratic_volatility=0.0020,
                      liquidity_profile="normal", event_sensitivity=1.08, mean_reversion=0.023, price_cache_factor_loading=0.66),
    AssetFactorConfig(ticker="S12", company_name="Mid-Cap Growth Proxy", sector="Synthetic",
                      initial_price_ticks=12_500, shares_outstanding=30_000_000,
                      initial_fundamental_value_ticks=12_500, macro_beta=0.52, idiosyncratic_volatility=0.0024,
                      liquidity_profile="normal", event_sensitivity=1.10, mean_reversion=0.028, price_cache_factor_loading=0.55),
    AssetFactorConfig(ticker="S13", company_name="Small-Cap Value Proxy", sector="Synthetic",
                      initial_price_ticks=10_000, shares_outstanding=20_000_000,
                      initial_fundamental_value_ticks=10_000, macro_beta=0.42, idiosyncratic_volatility=0.0028,
                      liquidity_profile="thin", event_sensitivity=1.18, mean_reversion=0.031, price_cache_factor_loading=0.45),
])

EXPANDED_UNIVERSE_PRESETS: dict[str, tuple[AssetFactorConfig, ...]] = {
    "eight_assets": EIGHT_ASSET_UNIVERSE,
    "twelve_assets": TWELVE_ASSET_UNIVERSE,
    "custom_csv": tuple(),
}


def _resolve_asset_universe(
    asset_count: int = 3,
    universe_preset: str | None = None,
    universe_csv: "str | Path | None" = None,
    strategy_asset: str = "SYNTH",
) -> "tuple[tuple[AssetFactorConfig, ...], str]":
    if asset_count < 1:
        raise ValueError("asset_count must be >= 1")
    if universe_csv:
        source = Path(universe_csv)
        if not source.exists():
            raise FileNotFoundError(f"Universe CSV not found: {universe_csv}")
        asset_lines = source.read_text(encoding="utf-8").splitlines()
        asset_lines = [line.strip() for line in asset_lines if line.strip() and not line.strip().startswith("#")]
        if not asset_lines:
            raise ValueError(f"No asset definitions found in {universe_csv}")
        asset_specs = [_parse_universe_csv_row(line) for line in asset_lines[1:asset_count + 1]]
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
            ticker = f"S{len(asset_specs):02d}"
            asset_specs.append(
                AssetFactorConfig(
                    ticker=ticker,
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
        preset_assets = EXPANDED_UNIVERSE_PRESETS[universe_preset]
        asset_specs = list(preset_assets[:asset_count])
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
            ticker = f"S{len(asset_specs):02d}"
            asset_specs.append(
                AssetFactorConfig(
                    ticker=ticker,
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

    if asset_count <= len(DEFAULT_ASSETS):
        return DEFAULT_ASSETS[:asset_count], strategy_asset
    asset_specs = list(DEFAULT_ASSETS)
    for index in range(len(DEFAULT_ASSETS), asset_count):
        ticker = f"S{index:02d}"
        asset_specs.append(
            AssetFactorConfig(
                ticker=ticker,
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
    ticker, company_name, sector, price_tick_str, shares_str, fv_str, beta_str, idios_vol_str, liquidity_profile = fields[:9]
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


def build_world(
    regime_key: str,
    seed: int,
    target_asset: str = "SYNTH",
    asset_count: int = 3,
    universe_preset: "str | None" = None,
    universe_csv: "str | Path | None" = None,
    price_cache: "dict[str, Sequence[float]] | None" = None,
    prices_cache: "dict[str, Sequence[float]] | None" = None,
    use_price_cache: bool = False,
    one_factor_gbm_config: OneFactorGBMConfig | None = None,
    calibration_pack: Any = None,
) -> WorldSpec:
    cfg = REGIME_CONFIGS[regime_key]
    vol_label = VOLATILITY_LABELS[min(int(cfg["vol_label_idx"]), len(VOLATILITY_LABELS) - 1)]
    resolved_assets, resolved_target = _resolve_asset_universe(
        asset_count=asset_count,
        universe_preset=universe_preset,
        universe_csv=universe_csv,
        strategy_asset=target_asset,
    )
    target_asset = resolved_target
    macro_factor_strength = max(0.1, 0.9 / max(asset_count, 1))
    exchange = ExchangeSpec(
        baseline_depth=int(cfg["depth"]),
        circuit_breaker_pct=15.0,
        halt_steps=6,
        book_depth_levels=5,
        latency_profile=cfg["latency"],
    )
    if calibration_pack is not None:
        from app.calibration.exchange_hooks import apply_calibration_pack_to_exchange

        exchange = apply_calibration_pack_to_exchange(exchange, calibration_pack)
    world = WorldSpec(
        world_id=f"fwd-{regime_key}-{seed}",
        seed=seed,
        clock=ClockSpec(
            start="2026-01-05T14:30:00Z",
            end="2026-01-05T15:30:00Z",
            step_seconds=30,
        ),
        macro=MacroSpec(
            volatility_regime=vol_label,
            risk_aversion=1.0 + cfg["vol_mult"] * 0.25,
            common_factor_strength=macro_factor_strength,
        ),
        assets=[
            AssetSpec(
                ticker=asset.ticker,
                company_name=asset.company_name,
                sector=asset.sector,
                initial_price_ticks=asset.initial_price_ticks,
                shares_outstanding=asset.shares_outstanding,
                initial_fundamental_value_ticks=asset.initial_fundamental_value_ticks,
                macro_beta=asset.macro_beta,
                idiosyncratic_volatility=asset.idiosyncratic_volatility * cfg["vol_mult"],
                liquidity_profile=asset.liquidity_profile,
                event_sensitivity=asset.event_sensitivity,
                mean_reversion=asset.mean_reversion,
            )
            for asset in resolved_assets
        ],
        exchange=exchange,
        agents=_build_agent_spec(asset_count),
        events=[],
        experiment=ExperimentSpec(
            strategy="twap",
            parent_order=ParentOrderSpec(side="buy", quantity=6_000),
            participation_rate=0.08,
            target_asset=target_asset,
            repetitions=1,
        ),
        interventions=InterventionSpec(forced_seller_quantity=0),
        calibration_pack_id=getattr(calibration_pack, "pack_id", None),
        calibration_parameter_set_id=(
            f"local-{getattr(calibration_pack, 'pack_id', 'none')[:12]}" if calibration_pack is not None else None
        ),
    )
    return world


def _build_agent_spec(asset_count: int) -> AgentsSpec:
    populations = [
        AgentPopulation(
            type="market_maker",
            count=3,
            capital_cents=500_000_000,
            latency_ms=2,
            risk_limit_shares=80_000,
            parameters={"spread_ticks": 5, "levels": 5, "inventory_skew": 0.002},
        ),
        AgentPopulation(
            type="fundamental",
            count=max(2, min(6, asset_count * 2)),
            capital_cents=120_000_000,
            latency_ms=20,
            risk_limit_shares=25_000,
        ),
        AgentPopulation(
            type="momentum",
            count=5,
            capital_cents=90_000_000,
            latency_ms=12,
            risk_limit_shares=20_000,
            parameters={"lookback": 4, "crowding": 1.0},
        ),
        AgentPopulation(
            type="mean_reversion",
            count=4,
            capital_cents=80_000_000,
            latency_ms=25,
            risk_limit_shares=18_000,
        ),
        AgentPopulation(
            type="noise",
            count=max(8, min(18, asset_count * 3)),
            capital_cents=30_000_000,
            latency_ms=40,
            risk_limit_shares=6_000,
        ),
        AgentPopulation(
            type="forced_liquidator",
            count=1,
            capital_cents=100_000_000,
            latency_ms=8,
            risk_limit_shares=150_000,
        ),
        AgentPopulation(
            type="execution",
            count=1,
            capital_cents=800_000_000,
            latency_ms=5,
            risk_limit_shares=250_000,
        ),
    ]
    return AgentsSpec(populations=populations)


_build_world = build_world

# Immutable WorldSpec prototypes keyed by (regime, asset_count, universe_preset, universe_csv, target).
_WORLD_PROTOTYPE_CACHE: dict[tuple[str, int, str, str, str], WorldSpec] = {}


def _universe_cache_key(
    *,
    regime_key: str,
    asset_count: int,
    universe_preset: str | None,
    universe_csv: str | Path | None,
    strategy_asset: str,
) -> tuple[str, int, str, str, str]:
    return (
        regime_key,
        int(asset_count),
        str(universe_preset or ""),
        str(universe_csv or ""),
        strategy_asset,
    )


def get_world_prototype(
    *,
    regime_key: str,
    asset_count: int = 3,
    universe_preset: str | None = None,
    universe_csv: str | Path | None = None,
    strategy_asset: str = "SYNTH",
) -> WorldSpec:
    """Return a cached immutable WorldSpec prototype for a regime/universe combo."""
    key = _universe_cache_key(
        regime_key=regime_key,
        asset_count=asset_count,
        universe_preset=universe_preset,
        universe_csv=universe_csv,
        strategy_asset=strategy_asset,
    )
    cached = _WORLD_PROTOTYPE_CACHE.get(key)
    if cached is not None:
        return cached
    prototype = build_world(
        regime_key,
        seed=0,
        target_asset=strategy_asset,
        asset_count=asset_count,
        universe_preset=universe_preset,
        universe_csv=universe_csv,
    )
    _WORLD_PROTOTYPE_CACHE[key] = prototype
    return prototype


def clone_world_from_prototype(prototype: WorldSpec, *, seed: int, regime_key: str) -> WorldSpec:
    """Clone a prototype WorldSpec with a fresh seed/world_id (deep copy)."""
    return prototype.model_copy(
        update={
            "seed": int(seed),
            "world_id": f"fwd-{regime_key}-{seed}",
        },
        deep=True,
    )


def clear_world_prototype_cache() -> None:
    """Test helper: drop cached WorldSpec prototypes."""
    _WORLD_PROTOTYPE_CACHE.clear()


def _strategy_artifact_digest(strategy_type: str, params: dict[str, int]) -> str:
    return hashlib.sha256(
        json.dumps({"type": strategy_type, "params": params}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _build_correlated_synthetic_paths(
    assets: tuple[AssetFactorConfig, ...],
    regime_key: str,
    seed: int,
    length: int,
    price_cache: "dict[str, Sequence[float]] | None",
    prices_cache: "dict[str, Sequence[float]] | None",
    use_price_cache: bool,
) -> dict[str, dict[str, object]]:
    generator = ResearchSyntheticMarketGenerator()
    price_cache_effective = price_cache if price_cache is not None else prices_cache
    price_cache_effective = price_cache_effective or {}
    base_prices = [float(asset.initial_price_ticks) for asset in assets]
    regime_keys_tuple = (regime_key,) * len(assets)
    target_assets = [asset.ticker for asset in assets]
    correlations = [
        float(asset.price_cache_factor_loading if asset.price_cache_factor_loading is not None else max(0.05, 0.9 - 0.03 * index))
        for index, asset in enumerate(assets)
    ]
    return generator.generate_correlated_paths(
        regime_keys=regime_keys_tuple,
        seed=seed,
        base_prices=base_prices,
        target_assets=target_assets,
        factor_correlations=correlations,
        price_cache=price_cache_effective,
        prices_cache=price_cache_effective,
        use_price_cache=use_price_cache,
    )


def _build_one_factor_paths_for_assets(
    assets: tuple[AssetFactorConfig, ...],
    regime_key: str,
    seed: int,
    length: int,
    price_cache: "dict[str, Sequence[float]] | None",
    prices_cache: "dict[str, Sequence[float]] | None",
    use_price_cache: bool,
    gbm_config: OneFactorGBMConfig | None,
) -> dict[str, dict[str, object]]:
    generator = ResearchSyntheticMarketGenerator()
    price_cache_effective = price_cache if price_cache is not None else prices_cache
    result: dict[str, dict[str, object]] = {}
    for index, asset in enumerate(assets):
        local_gbm = OneFactorGBMConfig(
            annual_drift=0.06,
            annual_volatility=float(asset.idiosyncratic_volatility * 252),
            correlation_to_market=float(asset.macro_beta * 0.85),
        )
        if gbm_config is not None:
            local_gbm = gbm_config
        result[asset.ticker] = generator.generate_one_factor_asset_path(
            ticker=asset.ticker,
            regime_key=regime_key,
            seed=seed + index,
            length=length,
            base_price=float(asset.initial_price_ticks),
            gbm_config=local_gbm,
            price_cache=price_cache_effective,
            prices_cache=price_cache_effective,
            use_price_cache=use_price_cache,
        )
    return result


class UserStrategyOrderRouter:
    STRATEGY_ORDER_ID_FORMAT = "user-strategy-o{order_index:07d}"

    def __init__(
        self,
        target_symbol: str,
        side: str,
        strategy_type: str,
        params: dict[str, int],
        lot_size: int = 1,
        base_quantity: int = 100,
        order_type: str = "limit",
        order_ttl_steps: "int | None" = None,
    ) -> None:
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy/sell")
        if order_type not in {"limit", "market", "ioc"}:
            raise ValueError("order_type must be limit/market/ioc")
        self.target_symbol = target_symbol
        self.side = side
        self.strategy_type = strategy_type
        self.params = dict(params)
        self.lot_size = lot_size
        self.base_quantity = ((base_quantity // lot_size) * lot_size) or lot_size
        self.order_type = order_type
        self.order_ttl_steps = order_ttl_steps
        self.order_index = 0
        self._last_order_id_template = self.STRATEGY_ORDER_ID_FORMAT

    def next_order_id(self) -> str:
        self.order_index += 1
        return self._last_order_id_template.format(order_index=self.order_index)

    def decide(self, observation: dict[str, Any]) -> dict[str, Any]:
        if not observation or str(observation.get("symbol")) != self.target_symbol:
            return {"action_type": "hold"}

        history = observation.get("recent_prices") or []
        if len(history) < 3:
            return {"action_type": "hold"}

        inventory = int(observation.get("inventory") or 0)
        remaining = int(observation.get("remaining_quantity") or 0)
        if remaining <= 0:
            return {"action_type": "hold"}

        from app.break_test.strategies import compute_positions

        prices = np.asarray(list(history), dtype=float)
        try:
            positions = compute_positions(self.strategy_type, prices, **self.params)
        except ValueError:
            return {"action_type": "hold"}
        if positions.size == 0 or observation.get("step", 0) >= positions.size:
            return {"action_type": "hold"}

        desired = float(positions[int(observation["step"])])
        if desired <= 0 or remaining <= 0:
            return {"action_type": "hold", "schema_version": "1.0", "side": self.side, "quantity": 0}

        quantity = max(self.lot_size, min(self.base_quantity, remaining))
        quantity = (quantity // self.lot_size) * self.lot_size
        if quantity <= 0:
            return {"action_type": "hold"}

        limit_price_ticks = None
        if self.order_type == "limit":
            mid_prices = [p for p in history[-5:] if isinstance(p, (int, float)) and p > 0]
            limit_price_ticks = int(np.median(mid_prices)) if mid_prices else int(history[-1])
            limit_price_ticks = max(1, limit_price_ticks)

        return {
            "action_type": self.order_type,
            "side": self.side,
            "quantity": quantity,
            "limit_price_ticks": limit_price_ticks,
            "ttl_steps": self.order_ttl_steps,
            "synthetic": False,
        }

    def to_execution_decider(self):
        router = self
        observation_history: dict[str, list[float]] = {}

        def execution_decider(observation: dict[str, Any]) -> dict[str, Any]:
            symbol = str(observation.get("symbol") or self.target_symbol)
            if symbol == self.target_symbol:
                history = list(observation.get("recent_prices") or [])
                session_history = observation_history.setdefault(symbol, list(history))
                session_history.extend(x for x in history[len(session_history):] if isinstance(x, (int, float)))
                if "mid_ticks" in observation and observation["mid_ticks"]:
                    mid = float(observation["mid_ticks"])
                    if not observation_history[symbol] or observation_history[symbol][-1] != mid:
                        observation_history[symbol].append(mid)
                if len(observation_history.get(symbol, [])) >= 3:
                    patched = dict(observation)
                    patched["recent_prices"] = observation_history[symbol]
                    observation = patched
            action = router.decide(observation)
            if action.get("action_type") == "hold":
                return {"action_type": "hold"}
            side_value = action["side"]
            order_type_value = action.get("order_type", action.get("action_type", "limit"))
            submit_action_type = order_type_value if order_type_value in {"limit", "market"} else "limit"
            return {
                "action_type": submit_action_type,
                "side": side_value,
                "quantity": action["quantity"],
                "limit_price_ticks": action.get("limit_price_ticks") if submit_action_type == "limit" else None,
                "rationale_code": "forward_execution_router",
            }

        return execution_decider


def _strategy_agent_id(sim: Any) -> str:
    return next(
        (
            row["agent_id"]
            for row in getattr(sim, "agent_states", [])
            if row.get("agent_type") == "execution"
        ),
        "execution-01",
    )


def forward_world_seed(regime_index: int, world_idx: int) -> int:
    """Deterministic seed for a (regime, world) pair.

    Seeds are partitioned by regime/world index only — never by worker id —
    so ``workers=1`` and ``workers=N`` evaluate the same seed set.
    """
    return 40_000 + int(regime_index) * 1_000 + int(world_idx)


def _simulate_forward_world(job: dict[str, Any]) -> dict[str, Any] | None:
    """Process-pool worker: run one cloned world and return rollup fields."""
    try:
        from app.simulation import run_simulation as _run
    except ImportError:  # pragma: no cover
        return None
    world = job.get("world")
    if world is None:
        prototype = job["prototype"]
        world = clone_world_from_prototype(
            prototype,
            seed=int(job["seed"]),
            regime_key=str(job["regime_key"]),
        )
        world.experiment.target_asset = job["resolved_target"]
    execution_decider = None
    if job.get("forward_execution_mode") == "real":
        router = UserStrategyOrderRouter(
            target_symbol=job["resolved_target"],
            side=str(getattr(world.experiment.parent_order.side, "value", world.experiment.parent_order.side)),
            strategy_type=job["strategy_type"],
            params=job["params"],
            lot_size=int(getattr(world.exchange, "lot_size", 1) or 1),
            base_quantity=max(50, int(getattr(world.experiment.parent_order, "quantity", 1000) * 0.1)),
            order_type="limit",
            order_ttl_steps=job.get("order_ttl_steps"),
        )
        execution_decider = router.to_execution_decider()
    try:
        sim = _run(
            world,
            execution_decider=execution_decider,
            exchange_engine=job.get("exchange_engine", DEFAULT_EXCHANGE_ENGINE),
            collect_timeline=job.get("collect_timeline", True),
            collect_agent_states=job.get("collect_agent_states", True),
            collect_strategy_steps=job.get("collect_strategy_steps", False),
            strategy_artifact_digest=job.get("strategy_digest"),
        )
    except Exception:
        return None

    resolved_target = job["resolved_target"]
    strategy_id = _strategy_agent_id(sim)
    target_trades = [
        trade
        for trade in sim.trades
        if trade.get("symbol") == resolved_target
        and (trade.get("buyer_id") == strategy_id or trade.get("seller_id") == strategy_id)
    ]
    world_fills = [int(trade.get("quantity", 0)) for trade in target_trades]
    world_inventory = 0
    for agent_state in sim.agent_states:
        if agent_state.get("agent_id") == strategy_id:
            world_inventory = int(agent_state.get("inventory", {}).get(resolved_target, 0) or 0)

    target_mids = []
    for frame in sim.timeline:
        state = frame.get("asset_states", {}).get(resolved_target, {})
        mid = state.get("mid_ticks")
        if mid is not None:
            target_mids.append(float(mid))
    params = job["params"]
    strategy_type = job["strategy_type"]
    min_len = max(
        10,
        params.get("slow", 50) if strategy_type == "sma_crossover" else params.get("entry_lookback", 20),
    )
    if len(target_mids) < min_len:
        return None
    position_series = _build_position_series_from_sim(sim, strategy_id, resolved_target, len(target_mids))
    if position_series.size < len(target_mids):
        position_series = np.pad(position_series, (0, len(target_mids) - position_series.size), mode="edge")
    result = backtest_metrics(np.asarray(target_mids, dtype=float), position_series)
    return {
        "total_return_pct": float(result["total_return_pct"]),
        "max_drawdown_pct": float(result["max_drawdown_pct"]),
        "fill_qty": sum(world_fills),
        "fill_count": len(world_fills),
        "inventory": world_inventory,
        "tca_by_bucket": result.get("tca_by_bucket", []),
        "slippage_vs_vwap": result.get("slippage_vs_vwap"),
        "slippage_vs_arrival": result.get("slippage_vs_arrival"),
        "opportunity_cost": result.get("opportunity_cost"),
        "completion_rate_penalty_bps": result.get("completion_rate_penalty_bps"),
    }


def run_exchange_forward_test(
    closes: list[float],
    strategy_type: str,
    params: dict[str, int],
    worlds_per_regime: int = 10,
    asset_count: int = 3,
    universe_preset: "str | None" = None,
    universe_csv: "str | Path | None" = None,
    forward_execution_mode: str = "real",
    order_ttl_steps: "int | None" = None,
    exchange_engine: str = DEFAULT_EXCHANGE_ENGINE,
    collect_timeline: bool = True,
    collect_agent_states: bool = True,
    collect_strategy_steps: bool = False,
    workers: int = 1,
    calibration_pack: Any = None,
) -> list[dict[str, object]]:
    prices = np.asarray(closes, dtype=float)
    _ = prices
    regime_keys = list(REGIME_CONFIGS.keys())
    results: list[dict[str, object]] = []
    label_map = {
        "steady_trend": "Steady Trend",
        "sideways_choppy": "Sideways & Choppy",
        "high_volatility": "High Volatility",
        "sudden_selloff": "Sudden Selloff",
    }
    resolved_assets, resolved_target = _resolve_asset_universe(
        asset_count=asset_count,
        universe_preset=universe_preset,
        universe_csv=universe_csv,
        strategy_asset="SYNTH",
    )
    _ = resolved_assets
    strategy_digest = _strategy_artifact_digest(strategy_type, params)
    worker_count = max(1, int(workers))

    for regime_index, regime_key in enumerate(regime_keys):
        prototype = get_world_prototype(
            regime_key=regime_key,
            asset_count=asset_count,
            universe_preset=universe_preset,
            universe_csv=universe_csv,
            strategy_asset=resolved_target,
        )
        if calibration_pack is not None:
            from app.calibration.exchange_hooks import apply_calibration_pack_to_world

            prototype = apply_calibration_pack_to_world(prototype, calibration_pack)

        # Seed list is fixed by (regime_index, world_idx). Worker assignment
        # must not change which seed each world_idx evaluates.
        jobs: list[dict[str, Any]] = []
        for world_idx in range(worlds_per_regime):
            seed = forward_world_seed(regime_index, world_idx)
            jobs.append(
                {
                    "prototype": prototype,
                    "seed": seed,
                    "regime_key": regime_key,
                    "resolved_target": resolved_target,
                    "strategy_type": strategy_type,
                    "params": params,
                    "forward_execution_mode": forward_execution_mode,
                    "order_ttl_steps": order_ttl_steps,
                    "exchange_engine": exchange_engine,
                    "collect_timeline": collect_timeline,
                    "collect_agent_states": collect_agent_states,
                    "collect_strategy_steps": collect_strategy_steps,
                    "strategy_digest": strategy_digest,
                }
            )

        world_rows: list[dict[str, Any]] = []
        if worker_count == 1:
            for job in jobs:
                row = _simulate_forward_world(job)
                if row is not None:
                    world_rows.append(row)
        else:
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor

            # spawn keeps worker imports clean on macOS; map preserves job order.
            ctx = mp.get_context("spawn")
            with ProcessPoolExecutor(max_workers=worker_count, mp_context=ctx) as pool:
                for row in pool.map(_simulate_forward_world, jobs):
                    if row is not None:
                        world_rows.append(row)

        if not world_rows:
            continue
        regime_returns = [float(row["total_return_pct"]) for row in world_rows]
        regime_drawdowns = [float(row["max_drawdown_pct"]) for row in world_rows]
        losses = sum(1 for value in regime_returns if value < 0)
        fill_sets = [(int(row["fill_qty"]), int(row["fill_count"])) for row in world_rows]
        inventory_sets = [int(row["inventory"]) for row in world_rows]
        results.append({
            "regime": label_map.get(regime_key, regime_key),
            "worlds": len(regime_returns),
            "loss_rate_pct": round(float(losses) / len(regime_returns) * 100, 1),
            "median_return_pct": round(float(np.median(regime_returns)), 2),
            "mean_return_pct": round(float(np.mean(regime_returns)), 2),
            "worst_drawdown_pct": round(float(np.min(regime_drawdowns)), 2),
            "best_return_pct": round(float(np.max(regime_returns)), 2),
            "fill_worlds": sum(1 for fills, count in fill_sets if count > 0),
            "total_fills": sum(count for _, count in fill_sets),
            "total_quantity": sum(qty for qty, _ in fill_sets),
            "inventory_changed_worlds": sum(
                1 for idx, inventory in enumerate(inventory_sets) if fill_sets[idx][0] > 0 and inventory != 0
            ),
            "order_execution_mode": forward_execution_mode,
            "exchange_engine": exchange_engine,
            "workers": worker_count,
            "slippage_vs_vwap": round(float(np.nanmean([row.get("slippage_vs_vwap") or 0.0 for row in world_rows])), 4),
            "slippage_vs_arrival": round(
                float(np.nanmean([row.get("slippage_vs_arrival") or 0.0 for row in world_rows])), 4
            ),
            "opportunity_cost": round(
                float(np.nanmean([row.get("opportunity_cost") or 0.0 for row in world_rows])), 4
            ),
            "completion_rate_penalty_bps": round(
                float(np.nanmean([row.get("completion_rate_penalty_bps") or 0.0 for row in world_rows])), 4
            ),
        })
    return results


def _build_position_series_from_sim(sim: Any, strategy_id: str, target_symbol: str, target_length: int) -> np.ndarray:
    series = np.zeros(target_length, dtype=float)
    executed = 0
    target_trades = [
        trade for trade in sim.trades
        if trade.get("symbol") == target_symbol
        and (trade.get("buyer_id") == strategy_id or trade.get("seller_id") == strategy_id)
    ]
    for trade in target_trades:
        step = int(trade.get("exchange_arrival_step") or trade.get("step") or 0)
        if 0 <= step < target_length:
            if trade.get("buyer_id") == strategy_id:
                executed += int(trade.get("quantity", 0))
            elif trade.get("seller_id") == strategy_id:
                executed -= int(trade.get("quantity", 0))
            series[step:] = float(np.clip(executed, -1, 1))
    return series
