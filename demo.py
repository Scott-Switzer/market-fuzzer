import os
import random
import sys
import time
from typing import Any

if any(part.startswith("/Users/scottthomasswitzer/.hermes") or ".hermes" in part for part in sys.path if "site-packages" in part):
    sys.path = [part for part in sys.path if not (".hermes" in part and "site-packages" in part)]


import numpy as np
import yfinance as yf

from app.break_test.data_loader import load_yfinance
from app.break_test.exchange_fwd import EXPANDED_UNIVERSE_PRESETS, REGIME_CONFIGS
from app.break_test.metrics import backtest_metrics, compute_equity_curve
from app.break_test.regimes import detect_regimes
from app.break_test.reporting import build_failure_report
from app.break_test.strategies import compute_positions
from app.schemas import (
    AgentPopulation,
    AgentsSpec,
    AssetSpec,
    ClockSpec,
    ExchangeSpec,
    ExperimentSpec,
    InterventionSpec,
    MacroSpec,
    ParentOrderSpec,
    WorldSpec,
)
from app.simulation import run_simulation

TICKERS = ["AAPL", "MSFT", "GOOGL"]
LOOKBACK_YEARS = int(os.environ.get("OAI_DEMO_LOOKBACK_YEARS", "3"))
WORLDS_PER_REGIME = int(os.environ.get("OAI_DEMO_WORLDS_PER_REGIME", "5"))
SIM_MINUTES = int(os.environ.get("OAI_DEMO_SIM_MINUTES", "30"))
_FALLBACK_DIR = os.path.join(os.path.dirname(__file__), "data", "yfinance_fallback")
_FALLBACK_SEED_BASIS = "demo_20y_v1"
_FORCE_SYNTHETIC_PRIMARY = os.environ.get("OAI_DEMO_FORCE_SYNTHETIC_PRIMARY", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)



def _fmt(target: Any) -> str:
    import datetime as dt

    return target.astimezone(dt.UTC).strftime("%Y-%m-%d")


def _fallback_path(ticker: str) -> str:
    os.makedirs(_FALLBACK_DIR, exist_ok=True)
    return os.path.join(_FALLBACK_DIR, f"{ticker}.csv")


def _write_fallback_csv(ticker: str, prices: list[float]) -> str:
    path = _fallback_path(ticker)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("close\n")
        for value in prices:
            handle.write(f"{value:.6f}\n")
    return path


def _read_fallback_csv(ticker: str) -> list[float]:
    path = _fallback_path(ticker)
    if not os.path.exists(path):
        return []
    values: list[float] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.lower().startswith("close"):
                continue
            values.append(float(stripped))
    return values


def _generate_deterministic_prices(ticker: str, years: int) -> list[float]:
    rng = random.Random(hash((ticker, years, _FALLBACK_SEED_BASIS)) & 0xFFFFFFFF)
    days = max(years * 252, 60)
    price = 50.0 if ticker == "GOOGL" else 120.0 if ticker == "MSFT" else 95.0
    prices: list[float] = []
    mu = 0.08 / 252.0
    sigma = 0.22 / (days**0.5)
    for _ in range(days):
        change = rng.gauss(mu, sigma)
        price = max(price * (1.0 + change), 1.0)
        prices.append(float(price))
    return prices


def load_primary(tickers: list[str], years: int) -> np.ndarray:
    if _FORCE_SYNTHETIC_PRIMARY:
        print(f"Using synthetic primary price series for {tickers} because OAI_DEMO_FORCE_SYNTHETIC_PRIMARY is set.")
        closes = _generate_deterministic_prices(tickers[0], years)
        print(f"Synthetic fallback prices for {tickers[0]} with documented seed basis {_FALLBACK_SEED_BASIS}.")
        path = _write_fallback_csv(tickers[0], closes)
        print(f"Saved {tickers[0]} primary series to {path}.")
        return np.asarray(closes, dtype=float)
    end = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    start_dt = __import__("datetime").datetime(end.year - years, end.month, end.day, tzinfo=__import__("datetime").timezone.utc)
    out = np.array([], dtype=float)
    fallback_note = ""
    for ticker in tickers[:1]:
        closes: list[float] = []
        try:
            df = yf.Ticker(ticker).history(
                start=_fmt(start_dt),
                end=_fmt(end),
                interval="1d",
                auto_adjust=True,
                timeout=30,
            )
            if df is not None and not df.empty:
                closes = [float(value) for value in df["Close"].dropna().to_numpy()]
        except Exception as exc:
            print(f"yfinance failed for {ticker}: {exc!r}")
        if not closes:
            closes = _read_fallback_csv(ticker)
        if not closes:
            try:
                closes = load_yfinance(
                    ticker,
                    start=_fmt(start_dt),
                    end=_fmt(end),
                    interval="1d",
                    auto_adjust=True,
                )
            except Exception as exc:
                print(f"Data-loader fallback failed for {ticker}: {exc!r}")
        if not closes:
            print(f"Using direct Yahoo Finance HTTP fallback for {ticker}.")
            closes = _load_yahoo_direct(ticker, _fmt(start_dt), _fmt(end))
        if not closes:
            print(f"Generating deterministic fallback prices for {ticker}.")
            closes = _generate_deterministic_prices(ticker, years)
            fallback_note = f"{ticker}: synthetic fallback with documented seed basis {_FALLBACK_SEED_BASIS}."
        if closes:
            written = _write_fallback_csv(ticker, closes)
            print(f"Saved {ticker} primary series to {written}.")
            out = np.asarray(closes, dtype=float)
        else:
            raise RuntimeError(f"No usable price history for {ticker}")
    if fallback_note:
        print(fallback_note)
    return out


def _load_yahoo_direct(ticker: str, start: str, end: str) -> list[float]:
    import urllib.parse
    import urllib.request

    base = "https://query1.finance.yahoo.com/v7/finance/download/"
    query = urllib.parse.urlencode(
        {
            "period1": str(int(__import__("datetime").datetime.fromisoformat(start).timestamp())),
            "period2": str(int(__import__("datetime").datetime.fromisoformat(end).timestamp())),
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
    )
    url = f"{base}{urllib.parse.quote(ticker)}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        text = response.read().decode("utf-8", errors="ignore")
    values: list[float] = []
    for line in text.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 5:
            continue
        close_value = parts[4].strip()
        if not close_value:
            continue
        try:
            values.append(float(close_value))
        except ValueError:
            continue
    if not values:
        raise RuntimeError(f"Direct Yahoo download returned no closes for {ticker}")
    return values


def build_world_demo(regime: str, seed: int, minutes: int = 60) -> WorldSpec:
    cfg = REGIME_CONFIGS[regime]
    vol_labels = ["low", "normal", "elevated", "crisis"]
    vol_label = vol_labels[min(int(cfg["vol_label_idx"]), 3)]
    import datetime as dt
    start_base = dt.datetime(2026, 1, 5, 14, 30, tzinfo=dt.UTC)
    asset_count = 8
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

    populations = [
        AgentPopulation(type="market_maker", count=3, capital_cents=500_000_000, latency_ms=2, risk_limit_shares=80_000, parameters={"spread_ticks": 5, "levels": 5, "inventory_skew": 0.002}),
        AgentPopulation(type="fundamental", count=max(2, min(6, asset_count * 2)), capital_cents=120_000_000, latency_ms=20, risk_limit_shares=25_000),
        AgentPopulation(type="momentum", count=5, capital_cents=90_000_000, latency_ms=12, risk_limit_shares=20_000, parameters={"lookback": 4, "crowding": 1.0}),
        AgentPopulation(type="mean_reversion", count=4, capital_cents=80_000_000, latency_ms=25, risk_limit_shares=18_000),
        AgentPopulation(type="noise", count=max(8, min(18, asset_count * 3)), capital_cents=30_000_000, latency_ms=40, risk_limit_shares=6_000),
        AgentPopulation(type="forced_liquidator", count=1, capital_cents=100_000_000, latency_ms=8, risk_limit_shares=150_000),
        AgentPopulation(type="execution", count=1, capital_cents=800_000_000, latency_ms=5, risk_limit_shares=250_000),
    ]
    return WorldSpec(
        world_id=f"demo-{regime}-{seed}",
        seed=seed,
        clock=ClockSpec(start=start_base, end=start_base + dt.timedelta(minutes=minutes), step_seconds=30),
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
        agents=AgentsSpec(populations=populations),
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


def extract_prices(sim: Any, target: str = "AAPL") -> np.ndarray:
    mids = [
        float(frame.get("asset_states", {}).get(target, {}).get("mid_ticks", 0.0) or 0.0)
        for frame in getattr(sim, "timeline", [])
    ]
    mids = [m for m in mids if m > 0]
    return np.asarray(mids, dtype=float)


def main() -> None:
    t0 = time.perf_counter()
    primary = load_primary(TICKERS, LOOKBACK_YEARS)
    print(f"Loaded {primary.size} closes for {TICKERS[0]}.")

    params = {"fast": 20, "slow": 50}
    hist_positions = compute_positions("sma_crossover", primary, **params)
    historical = backtest_metrics(primary, hist_positions)
    compute_equity_curve(primary, hist_positions)
    regime_detect = detect_regimes(primary.tolist())

    current_signal = "LONG" if hist_positions[-1] > 0.5 else "FLAT"
    print(
        f"Regime: {regime_detect.get('regime')} | Signal: {current_signal} | Price: ${float(primary[-1]):.2f}"
    )
    print(
        f"Return: {float(historical['total_return_pct']):.2f}% | DD: {float(historical['max_drawdown_pct']):.2f}% | Sharpe: {float(historical['sharpe']):.2f}"
    )

    regimes = ["steady_trend", "sideways_choppy", "high_volatility", "sudden_selloff"]
    summaries: list[dict[str, Any]] = []
    for regime in regimes:
        returns: list[float] = []
        drawdowns: list[float] = []
        losses = 0
        completed = 0
        for idx in range(WORLDS_PER_REGIME):
            seed = 40_000 + regimes.index(regime) * 1_000 + idx
            try:
                world = build_world_demo(regime, seed=seed, minutes=30)
                sim = run_simulation(world)
            except Exception as exc:
                print(f"  skip seed={seed}: {exc!r}")
                continue
            syn = extract_prices(sim, "AAPL")
            if syn.size < max(10, params.get("slow", 50)):
                continue
            positions = compute_positions("sma_crossover", syn, **params)
            metrics = backtest_metrics(syn, positions)
            completed += 1
            value = float(metrics["total_return_pct"])
            returns.append(value)
            drawdowns.append(float(metrics["max_drawdown_pct"]))
            losses += value < 0
        if returns:
            summaries.append(
                {
                    "regime": regime.replace("_", " ").title(),
                    "worlds": completed,
                    "loss_rate_pct": float(losses) / float(completed) * 100.0,
                    "median_return_pct": float(np.median(returns)),
                    "mean_return_pct": float(np.mean(returns)),
                    "worst_drawdown_pct": float(min(drawdowns)),
                    "best_return_pct": float(max(returns)),
                }
            )

    forward = {
        "regimes": summaries,
        "overall_loss_rate_pct": float(np.mean([row["loss_rate_pct"] for row in summaries])) if summaries else 100.0,
    }
    report = build_failure_report("sma_crossover", params, historical, forward["regimes"])
    print("\nForward Test Results:")
    for row in forward["regimes"]:
        print(
            f"{row['regime']}: worlds={row['worlds']}, median={row['median_return_pct']:.2f}%, worstDD={row['worst_drawdown_pct']:.2f}%, loss={row['loss_rate_pct']:.1f}%"
        )

    suggestion = report.get("correction_suggestion") or {}
    alternatives = suggestion.get("alternatives") if isinstance(suggestion, dict) else None
    print("\nFailure summary:", report.get("failure_summary"))
    if alternatives:
        print("Alternatives:")
        for alt in alternatives[:3]:
            if isinstance(alt, dict):
                print(f" - {alt.get('label')}: {alt.get('reason')} at {alt.get('parameter_changes')}")
    print(f"\nDemo wall time: {time.perf_counter() - t0:.2f} seconds")


if __name__ == "__main__":
    main()
