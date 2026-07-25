import time
from datetime import UTC

from app.simulation import run_simulation


def run(step_seconds=30, minutes=15):
    from datetime import datetime, timedelta

    from app.schemas import (
        AgentsSpec,
        ClockSpec,
        ExchangeSpec,
        ExperimentSpec,
        InterventionSpec,
        MacroSpec,
        ParentOrderSpec,
        WorldSpec,
    )
    from app.schemas.world import AgentPopulation as AP
    from app.schemas.world import AssetSpec

    target = "AAPL"
    asset_count = 8
    regime = "steady_trend"
    seed = 40001
    cfg = {"vol_mult": 0.7, "vol_label_idx": 1, "latency": "normal", "depth": 600}
    from app.break_test.exchange_fwd import EXPANDED_UNIVERSE_PRESETS

    assets_raw = list(EXPANDED_UNIVERSE_PRESETS.get("eight_assets", tuple()))[:asset_count]
    tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM"]
    assets = [
        AssetSpec(
            ticker=t,
            company_name=a.company_name,
            sector=a.sector,
            initial_price_ticks=a.initial_price_ticks,
            shares_outstanding=a.shares_outstanding,
            initial_fundamental_value_ticks=a.initial_fundamental_value_ticks,
            macro_beta=a.macro_beta,
            idiosyncratic_volatility=float(a.idiosyncratic_volatility * float(cfg["vol_mult"])),
            liquidity_profile=a.liquidity_profile,
            event_sensitivity=a.event_sensitivity,
            mean_reversion=a.mean_reversion,
        )
        for a, t in zip(assets_raw, tickers, strict=False)
    ]
    start = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    end = start + timedelta(minutes=minutes)
    populations = [
        AP(
            type="market_maker",
            count=3,
            capital_cents=500_000_000,
            latency_ms=2,
            risk_limit_shares=80_000,
            parameters={"spread_ticks": 5, "levels": 5, "inventory_skew": 0.002},
        ),
        AP(
            type="fundamental",
            count=max(2, min(6, asset_count * 2)),
            capital_cents=120_000_000,
            latency_ms=20,
            risk_limit_shares=25_000,
        ),
        AP(
            type="momentum",
            count=5,
            capital_cents=90_000_000,
            latency_ms=12,
            risk_limit_shares=20_000,
            parameters={"lookback": 4, "crowding": 1.0},
        ),
        AP(type="mean_reversion", count=4, capital_cents=80_000_000, latency_ms=25, risk_limit_shares=18_000),
        AP(
            type="noise",
            count=max(8, min(18, asset_count * 3)),
            capital_cents=30_000_000,
            latency_ms=40,
            risk_limit_shares=6_000,
        ),
        AP(
            type="forced_liquidator",
            count=1,
            capital_cents=100_000_000,
            latency_ms=8,
            risk_limit_shares=150_000,
        ),
        AP(type="execution", count=1, capital_cents=800_000_000, latency_ms=5, risk_limit_shares=250_000),
    ]
    world = WorldSpec(
        world_id=f"perf-{regime}-{seed}",
        seed=seed,
        clock=ClockSpec(start=start, end=end, step_seconds=step_seconds),
        macro=MacroSpec(
            volatility_regime="normal",
            risk_aversion=1.0 + float(cfg["vol_mult"]) * 0.25,
            common_factor_strength=max(0.1, 0.9 / max(asset_count, 1)),
        ),
        assets=assets,
        exchange=ExchangeSpec(
            baseline_depth=int(cfg["depth"]),
            circuit_breaker_pct=15.0,
            halt_steps=6,
            book_depth_levels=5,
            latency_profile=cfg["latency"],
        ),
        agents=AgentsSpec(populations=populations),
        events=[],
        experiment=ExperimentSpec(
            strategy="twap",
            parent_order=ParentOrderSpec(side="buy", quantity=6_000),
            participation_rate=0.08,
            target_asset=target,
            repetitions=1,
        ),
        interventions=InterventionSpec(forced_seller_quantity=0),
    )
    t0 = time.perf_counter()
    sim = run_simulation(world)
    print("steps", len(sim.timeline), "time", round(time.perf_counter() - t0, 3), "s")


if __name__ == "__main__":
    run(minutes=15)
