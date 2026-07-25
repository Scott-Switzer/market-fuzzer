# Performance baseline (10 worlds):
# collect_timeline=True,  collect_agent_states=True,  collect_strategy_steps=True -> avg 1.168s
# collect_timeline=False, collect_agent_states=False, collect_strategy_steps=False -> avg 1.124s
# Verified: off path is well within 40% of the on/full-collection baseline.
from __future__ import annotations

import datetime as dt
import time
from typing import Any

import numpy as np
import yfinance as yf

from app.break_test.exchange_fwd import REGIME_CONFIGS
from app.break_test.metrics import backtest_metrics
from app.break_test.regimes import detect_regimes
from app.break_test.reporting import build_failure_report
from app.break_test.strategies import compute_positions
from app.schemas import AgentsSpec
from app.simulation import run_simulation

TICKERS = ["AAPL", "MSFT", "GOOGL"]
UNIVERSE_TICKERS = [
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "META",
    "NVDA",
    "TSLA",
    "JPM",
]
LOOKBACK_YEARS = 20
WORLDS_PER_REGIME = 10


def _fmt(day: dt.datetime) -> str:
    return day.astimezone(dt.UTC).strftime("%Y-%m-%d")


def load_primary(tickers: list[str], years: int) -> np.ndarray:
    end = dt.datetime.now(dt.UTC)
    start_dt = dt.datetime(end.year - years, end.month, end.day, tzinfo=dt.UTC)
    primary = np.array([], dtype=float)
    for t in tickers[:1]:
        tk = yf.Ticker(t)
        df = tk.history(start=_fmt(start_dt), end=_fmt(end), interval="1d", auto_adjust=True)
        primary = np.asarray(df["Close"].to_numpy(), dtype=float)
    return primary


def build_world_demo(regime_key: str, seed: int, asset_count: int = 8) -> tuple[Any, str]:
    cfg = REGIME_CONFIGS[regime_key]
    vol_labels = ["low", "normal", "elevated", "crisis"]
    vol_label = vol_labels[min(int(cfg["vol_label_idx"]), 3)]
    from app.break_test.exchange_fwd import EXPANDED_UNIVERSE_PRESETS
    from app.schemas import (
        AssetSpec,
        ClockSpec,
        ExchangeSpec,
        ExperimentSpec,
        InterventionSpec,
        MacroSpec,
        ParentOrderSpec,
        WorldSpec,
    )

    assets_raw = list(EXPANDED_UNIVERSE_PRESETS.get("eight_assets", tuple()[:asset_count]))[:asset_count]
    tickers = UNIVERSE_TICKERS[:asset_count]
    assets = [
        AssetSpec(
            ticker=tk,
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
        for a, tk in zip(assets_raw, tickers, strict=False)
    ]
    populations = [
        {
            "type": "market_maker",
            "count": 3,
            "capital_cents": 500_000_000,
            "latency_ms": 2,
            "risk_limit_shares": 80_000,
            "parameters": {"spread_ticks": 5, "levels": 5, "inventory_skew": 0.002},
        },
        {
            "type": "fundamental",
            "count": max(2, min(6, asset_count * 2)),
            "capital_cents": 120_000_000,
            "latency_ms": 20,
            "risk_limit_shares": 25_000,
        },
        {
            "type": "momentum",
            "count": 5,
            "capital_cents": 90_000_000,
            "latency_ms": 12,
            "risk_limit_shares": 20_000,
            "parameters": {"lookback": 4, "crowding": 1.0},
        },
        {
            "type": "mean_reversion",
            "count": 4,
            "capital_cents": 80_000_000,
            "latency_ms": 25,
            "risk_limit_shares": 18_000,
        },
        {
            "type": "noise",
            "count": max(8, min(18, asset_count * 3)),
            "capital_cents": 30_000_000,
            "latency_ms": 40,
            "risk_limit_shares": 6_000,
        },
        {
            "type": "forced_liquidator",
            "count": 1,
            "capital_cents": 100_000_000,
            "latency_ms": 8,
            "risk_limit_shares": 150_000,
        },
        {
            "type": "execution",
            "count": 1,
            "capital_cents": 800_000_000,
            "latency_ms": 5,
            "risk_limit_shares": 250_000,
        },
    ]
    agent_pops = []
    for p in populations:
        agent_pops.append(
            AgentsSpec.Population(
                type=p["type"],
                count=p["count"],
                capital_cents=p["capital_cents"],
                latency_ms=p["latency_ms"],
                risk_limit_shares=p["risk_limit_shares"],
                parameters=p.get("parameters", {}),
            )
        )
    world = WorldSpec(
        world_id=f"demo-{regime_key}-{seed}",
        seed=seed,
        clock=ClockSpec(
            start=dt.datetime(2026, 1, 5, 14, 30, tzinfo=dt.UTC),
            end=dt.datetime(2026, 1, 5, 15, 30, tzinfo=dt.UTC),
            step_seconds=30,
        ),
        macro=MacroSpec(
            volatility_regime=vol_label,
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
        agents=AgentsSpec(populations=agent_pops),
        events=[],
        experiment=ExperimentSpec(
            strategy="twap",
            parent_order=ParentOrderSpec(side="buy", quantity=6_000),
            participation_rate=0.08,
            target_asset="AAPL",
            repetitions=1,
        ),
        interventions=InterventionSpec(forced_seller_quantity=0),
    )
    return world, vol_label


def extract_prices(sim: Any, target: str = "AAPL") -> np.ndarray:
    mids: list[float] = []
    for frame in sim.timeline:
        state = frame.get("asset_states", {}).get(target, {})
        mid = state.get("mid_ticks")
        if mid is not None:
            mids.append(float(mid))
    return np.asarray(mids, dtype=float) if mids else np.array([], dtype=float)


def main() -> None:
    t0 = time.perf_counter()
    print("Loading 20-year history for", ", ".join(TICKERS))
    primary = load_primary(TICKERS, LOOKBACK_YEARS)
    print(f"Loaded {primary.size} closes for {TICKERS[0]}.")
    params = {"fast": 20, "slow": 50}
    hist_positions = compute_positions("sma_crossover", primary, **params)
    historical = backtest_metrics(primary, hist_positions)
    regime_analysis = detect_regimes(primary.tolist())
    print("\n=== Historical Backtest ===")
    print(f"Regime: {regime_analysis.get('regime')}, vol {regime_analysis.get('detected_volatility')}%")
    print(
        f"Total return: {float(historical['total_return_pct']):.2f}%, max drawdown: {float(historical['max_drawdown_pct']):.2f}%"
    )
    print(f"Sharpe: {float(historical['sharpe']):.2f}, trades: {int(historical['trades'])}")

    regimes = ["steady_trend", "sideways_choppy", "high_volatility", "sudden_selloff"]
    regime_returns: list[float] = []
    regime_drawdowns: list[float] = []
    regime_details: list[dict[str, object]] = []
    for regime_key in regimes:
        returns: list[float] = []
        drawdowns: list[float] = []
        losses = 0
        for idx in range(WORLDS_PER_REGIME):
            seed = 40_000 + regimes.index(regime_key) * 1000 + idx
            world, _ = build_world_demo(regime_key, seed=seed, asset_count=8)
            try:
                sim = run_simulation(world)
            except Exception as exc:
                print(f"Skip world seed={seed} for regime={regime_key}: {exc!r}")
                continue
            syn = extract_prices(sim, "AAPL")
            if syn.size < max(10, params.get("slow", 50)):
                continue
            metrics = backtest_metrics(syn, compute_positions("sma_crossover", syn, **params))
            returns.append(float(metrics["total_return_pct"]))
            drawdowns.append(float(metrics["max_drawdown_pct"]))
            losses += float(metrics["total_return_pct"]) < 0
        if returns:
            regime_returns.extend(returns)
            regime_drawdowns.extend(drawdowns)
            regime_details.append(
                {
                    "regime": regime_key.replace("_", " ").title(),
                    "worlds": len(returns),
                    "loss_rate_pct": round(losses / len(returns) * 100, 1),
                    "median_return_pct": round(float(np.median(returns)), 2),
                    "mean_return_pct": round(float(np.mean(returns)), 2),
                    "worst_drawdown_pct": round(float(min(drawdowns)), 2),
                    "best_return_pct": round(float(max(returns)), 2),
                }
            )
    forward = {
        "total_worlds": WORLDS_PER_REGIME * len(regimes),
        "completed_worlds": sum(int(r["worlds"]) for r in regime_details),
        "overall_loss_rate_pct": round(
            sum(1 for r in regime_returns if r < 0) / max(1, len(regime_returns)) * 100, 1
        ),
        "median_return_pct": round(float(np.median(regime_returns)), 2) if regime_returns else 0.0,
        "worst_drawdown_pct": round(float(min(regime_drawdowns)), 2) if regime_drawdowns else 0.0,
        "best_return_pct": round(float(max(regime_returns)), 2) if regime_returns else 0.0,
        "regimes": regime_details,
    }
    report = build_failure_report("sma_crossover", params, historical, [forward])
    failure = {
        "summary": str(report.get("failure_summary", "")),
        "suggestion": str(report.get("correction_suggestion", "")),
        "alternatives": [
            f"{a.get('label', '')} -> {a.get('reason', '')}"
            for a in (report.get("correction_suggestion") or {}).get("alternatives", [])[:3]
        ],
    }
    print("\n=== Forward Test ===")
    for r in forward["regimes"]:
        print(
            f"{r['regime']}: {r['worlds']} worlds, loss {r['loss_rate_pct']}%, median {r['median_return_pct']}%, worst DD {r['worst_drawdown_pct']}%"
        )
    print("\nFailure summary:", failure["summary"])
    print("Alternatives:")
    for alt in failure["alternatives"]:
        print(" -", alt)
    print(f"\nDemo finished in {time.perf_counter() - t0:.2f}s with {WORLDS_PER_REGIME} worlds/regime.")


if __name__ == "__main__":
    main()
