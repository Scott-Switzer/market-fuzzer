from __future__ import annotations

from copy import deepcopy

from app.schemas import (
    AgentPopulation,
    AgentsSpec,
    AssetSpec,
    EventSpec,
    ExchangeSpec,
    ExperimentSpec,
    MacroSpec,
    ParentOrderSpec,
    WorldSpec,
)
from app.schemas.world import demo_clock

SCENARIOS = ("normal", "liquidity_withdrawal", "earnings_shock", "crowded_unwind")


def build_demo_world(seed: int = 42) -> WorldSpec:
    return WorldSpec(
        world_id="fragile-small-cap-lab",
        seed=seed,
        clock=demo_clock(),
        macro=MacroSpec(volatility_regime="elevated", risk_aversion=1.35, common_factor_strength=0.45),
        assets=[
            AssetSpec(
                ticker="NOVA",
                company_name="Novara Systems",
                sector="Software",
                initial_price_ticks=10_000,
                shares_outstanding=48_000_000,
                initial_fundamental_value_ticks=10_100,
                macro_beta=1.25,
                idiosyncratic_volatility=0.0028,
                liquidity_profile="thin",
                event_sensitivity=1.25,
            ),
            AssetSpec(
                ticker="ORBT",
                company_name="Orbiton Industrial",
                sector="Industrials",
                initial_price_ticks=7_500,
                shares_outstanding=72_000_000,
                initial_fundamental_value_ticks=7_480,
                macro_beta=0.75,
                idiosyncratic_volatility=0.0018,
                liquidity_profile="normal",
            ),
            AssetSpec(
                ticker="VYNE",
                company_name="Veyna Therapeutics",
                sector="Biotechnology",
                initial_price_ticks=12_500,
                shares_outstanding=31_000_000,
                initial_fundamental_value_ticks=12_300,
                macro_beta=1.1,
                idiosyncratic_volatility=0.0035,
                liquidity_profile="thin",
                event_sensitivity=1.5,
            ),
        ],
        exchange=ExchangeSpec(baseline_depth=500, circuit_breaker_pct=12.0),
        agents=AgentsSpec(
            populations=[
                AgentPopulation(
                    type="market_maker",
                    count=3,
                    capital_cents=500_000_000,
                    latency_ms=2,
                    risk_limit_shares=80_000,
                    parameters={"spread_ticks": 4, "levels": 5, "inventory_skew": 0.002},
                ),
                AgentPopulation(
                    type="fundamental",
                    count=6,
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
                    type="noise", count=18, capital_cents=30_000_000, latency_ms=40, risk_limit_shares=6_000
                ),
                AgentPopulation(
                    type="forced_liquidator",
                    count=1,
                    capital_cents=100_000_000,
                    latency_ms=8,
                    risk_limit_shares=150_000,
                    parameters={"start_step": 62, "total_quantity": 12_000},
                ),
                AgentPopulation(
                    type="execution",
                    count=1,
                    capital_cents=800_000_000,
                    latency_ms=5,
                    risk_limit_shares=250_000,
                ),
            ]
        ),
        events=[
            EventSpec(
                event_id="nova-earnings",
                simulation_step=40,
                scope="asset",
                asset="NOVA",
                type="earnings",
                fundamental_effect_pct=-6.5,
                narrative="Novara reports weaker synthetic recurring revenue and guidance.",
            ),
        ],
        experiment=ExperimentSpec(
            strategy="twap",
            parent_order=ParentOrderSpec(side="buy", quantity=6_000),
            participation_rate=0.08,
            target_asset="NOVA",
            repetitions=2,
        ),
    )


def mutate_scenario(base: WorldSpec, scenario: str) -> tuple[WorldSpec, dict]:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; expected one of {SCENARIOS}")
    data = deepcopy(base.model_dump(mode="python"))
    data["world_id"] = f"{base.world_id}-{scenario}"
    changes: dict[str, object] = {"scenario": scenario, "constants": ["seed", "assets", "strategy", "clock"]}
    if scenario == "normal":
        data["events"] = []
        data["macro"]["volatility_regime"] = "normal"
        changes["changed"] = ["removed stress events", "normal volatility"]
    elif scenario == "liquidity_withdrawal":
        data["events"] = [
            {
                "event_id": "market-liquidity-withdrawal",
                "simulation_step": 45,
                "scope": "market",
                "asset": None,
                "type": "liquidity_withdrawal",
                "public_or_private": "public",
                "fundamental_effect_pct": 0.0,
                "liquidity_effect": 0.35,
                "narrative": "Synthetic market makers reduce displayed size after a risk-limit shock.",
            }
        ]
        changes["changed"] = ["displayed liquidity falls to 35% at step 45"]
    elif scenario == "earnings_shock":
        changes["changed"] = ["NOVA fundamental falls 6.5% at step 40"]
    else:
        data["events"] = [
            {
                "event_id": "crowded-unwind",
                "simulation_step": 50,
                "scope": "market",
                "asset": None,
                "type": "forced_liquidation",
                "public_or_private": "public",
                "fundamental_effect_pct": -1.0,
                "liquidity_effect": 0.55,
                "narrative": "Crowded momentum positions unwind while a forced seller enters NOVA.",
            }
        ]
        for population in data["agents"]["populations"]:
            if population["type"] == "momentum":
                population["parameters"]["crowding"] = 2.4
            if population["type"] == "forced_liquidator":
                population["parameters"]["total_quantity"] = 28_000
                population["parameters"]["start_step"] = 50
        changes["changed"] = [
            "momentum crowding 2.4x",
            "forced sale of 28,000 shares",
            "liquidity falls to 55%",
        ]
    return WorldSpec.model_validate(data), changes
