from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from app.break_test.synthetic_market import AssetFactorConfig


class UniverseCatalog:
    """Catalog of real yfinance tickers and synthetic assets with sector metadata."""

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, object]] = {}
        self._order: list[str] = []

    def add_yfinance_tickers(
        self,
        tickers: list[str],
        *,
        sector: str = "Unknown",
        industry: str = "",
        default_price_ticks: int = 10_000,
        default_shares: int = 50_000_000,
        default_beta: float = 0.5,
        default_idios: float = 0.002,
        liquidity: str = "normal",
    ) -> UniverseCatalog:
        for ticker in {t.upper().strip() for t in tickers if t.strip()}:
            if ticker not in self._entries:
                self._entries[ticker] = {
                    "ticker": ticker,
                    "sector": sector,
                    "industry": industry,
                    "initial_price_ticks": default_price_ticks,
                    "shares_outstanding": default_shares,
                    "macro_beta": default_beta,
                    "idiosyncratic_volatility": default_idios,
                    "liquidity_profile": liquidity,
                }
                self._order.append(ticker)
        return self

    def add_fenrix_assets(self, assets: tuple[AssetFactorConfig, ...]) -> UniverseCatalog:
        for asset in assets:
            ticker = str(asset.ticker)
            if ticker not in self._entries:
                self._entries[ticker] = {
                    "ticker": ticker,
                    "sector": asset.sector,
                    "industry": "",
                    "initial_price_ticks": asset.initial_price_ticks,
                    "shares_outstanding": asset.shares_outstanding,
                    "macro_beta": asset.macro_beta,
                    "idiosyncratic_volatility": asset.idiosyncratic_volatility,
                    "liquidity_profile": asset.liquidity_profile,
                    "company_name": asset.company_name,
                }
                self._order.append(ticker)
        return self

    def resolve(
        self,
        count: int = 100,
        *,
        strategy_asset: str = "SYNTH",
        rotation_seed: int | None = None,
    ) -> tuple[AssetFactorConfig, ...]:
        if count < 1:
            raise ValueError("count must be >= 1")
        tickers = list(self._order)
        if rotation_seed is not None and len(tickers) > count:
            rng = np.random.default_rng(int(rotation_seed) % (2**31 - 1))
            indices = np.arange(len(tickers))
            rng.shuffle(indices)
            tickers = [tickers[int(i)] for i in indices[:count]]

        specs: list[AssetFactorConfig] = []
        for ticker in tickers[:count]:
            entry = self._entries[ticker]
            specs.append(
                AssetFactorConfig(
                    ticker=str(entry["ticker"]),
                    company_name=str(entry.get("company_name") or entry["ticker"]),
                    sector=str(entry["sector"]),
                    initial_price_ticks=int(entry["initial_price_ticks"]),
                    shares_outstanding=int(entry["shares_outstanding"]),
                    initial_fundamental_value_ticks=int(entry["initial_price_ticks"]),
                    macro_beta=float(entry["macro_beta"]),
                    idiosyncratic_volatility=float(entry["idiosyncratic_volatility"]),
                    liquidity_profile=str(entry["liquidity_profile"]),
                    event_sensitivity=1.0,
                    mean_reversion=0.02,
                    price_cache_factor_loading=round(float(entry["macro_beta"]), 6),
                )
            )

        if not any(asset.ticker == strategy_asset for asset in specs):
            specs.insert(
                0,
                AssetFactorConfig(
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
                ),
            )
        return tuple(specs[:count])

    @property
    def ticker_count(self) -> int:
        return len(self._order)

    @property
    def tickers(self) -> list[str]:
        return list(self._order)


def _build_daily_macro_100_catalog() -> UniverseCatalog:
    catalog = UniverseCatalog()
    macro_tickers = [
        ("SPY", "Technology", "Large Cap Blend"),
        ("QQQ", "Technology", "Nasdaq 100"),
        ("IWM", "Financials", "Russell 2000"),
        ("DIA", "Financials", "Dow Jones"),
        ("VTI", "Technology", "Total Stock Market"),
        ("VOO", "Technology", "S&P 500"),
        ("IVV", "Technology", "S&P 500"),
        ("VXUS", "Technology", "International"),
        ("VEA", "Financials", "Developed Markets"),
        ("VWO", "Financials", "Emerging Markets"),
        ("AAPL", "Technology", "Consumer Electronics"),
        ("MSFT", "Technology", "Software"),
        ("GOOGL", "Communication Services", "Search/Cloud"),
        ("AMZN", "Consumer Discretionary", "E-commerce/Cloud"),
        ("NVDA", "Technology", "Semiconductors"),
        ("META", "Communication Services", "Social Media"),
        ("TSLA", "Consumer Discretionary", "Electric Vehicles"),
        ("BRK-B", "Financials", "Conglomerate"),
        ("LLY", "Health Care", "Pharmaceuticals"),
        ("AVGO", "Technology", "Semiconductors"),
        ("JPM", "Financials", "Banking"),
        ("JNJ", "Health Care", "Pharmaceuticals"),
        ("V", "Financials", "Payment Processing"),
        ("PG", "Consumer Staples", "Consumer Goods"),
        ("UNH", "Health Care", "Managed Care"),
        ("HD", "Consumer Discretionary", "Home Improvement"),
        ("MA", "Financials", "Payment Processing"),
        ("DIS", "Communication Services", "Media/Entertainment"),
        ("BAC", "Financials", "Banking"),
        ("ADBE", "Technology", "Software"),
        ("CRM", "Technology", "Software"),
        ("CMCSA", "Communication Services", "Media"),
        ("NFLX", "Communication Services", "Streaming"),
        ("KO", "Consumer Staples", "Beverages"),
        ("PEP", "Consumer Staples", "Snack Foods"),
        ("TMO", "Health Care", "Life Sciences"),
        ("ABT", "Health Care", "Medical Devices"),
        ("MRK", "Health Care", "Pharmaceuticals"),
        ("ACN", "Technology", "IT Consulting"),
        ("COST", "Consumer Staples", "Retail"),
        ("CSCO", "Technology", "Networking"),
        ("MDT", "Health Care", "Medical Devices"),
        ("DHR", "Health Care", "Medical Devices"),
        ("TXN", "Technology", "Semiconductors"),
        ("NEE", "Utilities", "Renewable Energy"),
        ("WMT", "Consumer Staples", "Retail"),
        ("BMY", "Health Care", "Pharmaceuticals"),
        ("QCOM", "Technology", "Semiconductors"),
        ("HON", "Industrials", "Conglomerate"),
        ("ORCL", "Technology", "Software"),
        ("AMGN", "Health Care", "Biotechnology"),
        ("IBM", "Technology", "IT Consulting"),
        ("BLK", "Financials", "Asset Management"),
        ("PM", "Consumer Staples", "Tobacco"),
        ("CAT", "Industrials", "Heavy Equipment"),
        ("DE", "Industrials", "Heavy Equipment"),
        ("GS", "Financials", "Investment Banking"),
        ("SPGI", "Financials", "Financial Data"),
        ("AXP", "Financials", "Credit Cards"),
        ("T", "Communication Services", "Telecommunications"),
        ("MS", "Financials", "Investment Banking"),
        ("LOW", "Consumer Discretionary", "Home Improvement"),
        ("BA", "Industrials", "Aerospace"),
        ("AMD", "Technology", "Semiconductors"),
        ("INTC", "Technology", "Semiconductors"),
        ("PLTR", "Technology", "Software"),
        ("UBER", "Technology", "Ridesharing"),
        ("ABNB", "Consumer Discretionary", "Lodging"),
        ("PANW", "Technology", "Cybersecurity"),
        ("SNOW", "Technology", "Software"),
        ("NET", "Technology", "Internet Infrastructure"),
        ("CRWD", "Technology", "Cybersecurity"),
        ("ZS", "Technology", "Cybersecurity"),
        ("FTNT", "Technology", "Cybersecurity"),
        ("NOW", "Technology", "Software"),
        ("TEAM", "Technology", "Software"),
        ("PYPL", "Technology", "Payment Processing"),
        ("SHOP", "Technology", "E-commerce"),
        ("ROKU", "Communication Services", "Streaming"),
        ("SNAP", "Communication Services", "Social Media"),
        ("PINS", "Communication Services", "Social Media"),
        ("TWLO", "Communication Services", "Cloud Communications"),
        ("MDB", "Technology", "Software"),
        ("DDOG", "Technology", "Software"),
        ("OKTA", "Technology", "Cybersecurity"),
        ("ZM", "Technology", "Video Communications"),
        ("SMCI", "Technology", "Hardware"),
        ("ARM", "Technology", "Semiconductor IP"),
        ("MRVL", "Technology", "Semiconductors"),
        ("LRCX", "Technology", "Semiconductor Equipment"),
        ("KLAC", "Technology", "Semiconductor Equipment"),
        ("AMAT", "Technology", "Semiconductor Equipment"),
        ("ADI", "Technology", "Semiconductors"),
        ("TEL", "Technology", "Electronic Components"),
        ("MU", "Technology", "Semiconductors"),
        ("WDC", "Technology", "Storage"),
        ("STX", "Technology", "Storage"),
        ("NXPI", "Technology", "Semiconductors"),
        ("ENPH", "Technology", "Solar Inverters"),
        ("FSLR", "Technology", "Solar Panels"),
        ("NKE", "Consumer Discretionary", "Apparel"),
        ("LULU", "Consumer Discretionary", "Apparel"),
        ("DECK", "Consumer Discretionary", "Footwear"),
        ("VFC", "Consumer Discretionary", "Apparel"),
        ("M", "Consumer Discretionary", "Apparel"),
        ("JWN", "Consumer Discretionary", "Apparel"),
        ("CHWY", "Consumer Discretionary", "Pet Supplies"),
        ("WBA", "Consumer Staples", "Pharmacy"),
        ("CNC", "Health Care", "Managed Care"),
        ("DOC", "Real Estate", "REITs"),
        ("PLD", "Real Estate", "REITs"),
        ("AMT", "Real Estate", "REITs"),
        ("EQIX", "Real Estate", "REITs"),
        ("SPG", "Real Estate", "REITs"),
        ("O", "Real Estate", "REITs"),
        ("XEL", "Utilities", "Regulated Utility"),
        ("DUK", "Utilities", "Regulated Utility"),
        ("SO", "Utilities", "Regulated Utility"),
        ("D", "Utilities", "Regulated Utility"),
    ]

    for ticker, sector, industry in macro_tickers:
        catalog.add_yfinance_tickers([ticker], sector=sector, industry=industry)

    return catalog


_DAILY_MACRO_100_CATALOG: UniverseCatalog | None = None


def get_daily_macro_100_catalog() -> UniverseCatalog:
    global _DAILY_MACRO_100_CATALOG
    if _DAILY_MACRO_100_CATALOG is None:
        _DAILY_MACRO_100_CATALOG = _build_daily_macro_100_catalog()
    return _DAILY_MACRO_100_CATALOG


def load_daily_macro_100_preset(
    asset_count: int = 100,
    *,
    start: str | None = None,
    end: str | None = None,
    lookback: str = "20y",
    cache_dir: str | Path | None = None,
) -> tuple[AssetFactorConfig, ...]:
    """Resolve the daily_macro_100 preset with yfinance download and artifact fallback."""
    catalog = get_daily_macro_100_catalog()
    if asset_count > catalog.ticker_count:
        asset_count = catalog.ticker_count

    try:
        from app.break_test.data_loader import load_yfinance_daily_bulk

        bulk = load_yfinance_daily_bulk(
            catalog.tickers[:asset_count],
            start=start,
            end=end,
            lookback=lookback,
            cache_dir=cache_dir,
        )
        if bulk.get("tickers"):
            return catalog.resolve(count=asset_count)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(
            "yfinance daily_macro_100 download failed, falling back to artifacts: %s", exc
        )

    try:
        cache_root = Path(cache_dir) if cache_dir is not None else Path("artifacts") / "yfinance_bulk"
        cache_files = sorted(cache_root.glob("daily_bulk_*.json"))
        if cache_files:
            with open(cache_files[-1], encoding="utf-8") as fh:
                cached = json.load(fh)
            if isinstance(cached, dict) and "tickers" in cached and cached["tickers"]:
                return catalog.resolve(count=asset_count)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("Artifact fallback failed for daily_macro_100: %s", exc)

    return catalog.resolve(count=asset_count)
