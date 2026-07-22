from __future__ import annotations

import logging
import warnings
from typing import Any

import numpy as np

try:
    import pandas as pd

    _PANDAS_AVAILABLE = True
except Exception:  # pragma: no cover
    pd = None  # type: ignore[assignment]
    _PANDAS_AVAILABLE = False

try:
    import yfinance as yf

    _YFINANCE_AVAILABLE = True
except Exception:  # pragma: no cover
    yf = None  # type: ignore[assignment]
    _YFINANCE_AVAILABLE = False

try:
    import requests  # type: ignore[import]

    _REQUESTS_AVAILABLE = True
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]
    _REQUESTS_AVAILABLE = False

try:
    from datetime import date, timedelta
except Exception:  # pragma: no cover
    date = None  # type: ignore[assignment,misc]
    timedelta = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

_MIN_BARS = 252 * 5


def _is_date(value: object) -> bool:
    return hasattr(value, "isoformat") and hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day")

def validate_prices(prices: list[float]) -> list[float]:
    arr = np.asarray(prices, dtype=float)
    if arr.ndim != 1 or arr.size < 80:
        raise ValueError("Provide at least 80 prices")
    if not np.all(np.isfinite(arr)) or np.any(arr <= 0):
        raise ValueError("Prices must be finite and positive")
    return arr.tolist()


def validate_prices_after_source(prices: list[float], *, min_length: int = 80) -> list[float]:
    arr = np.asarray(prices, dtype=float)
    if arr.ndim != 1 or arr.size < min_length:
        return arr.tolist()
    return validate_prices(prices)


def suggest_lookback(data_source: str | None, tickers: list[str] | None) -> str:
    if data_source == "yfinance" and tickers:
        return "20y"
    return "3y"


def warn_on_short_history(prices: list[float], *, min_bars: int = _MIN_BARS) -> dict[str, object]:
    warnings_list: list[str] = []
    helpful: dict[str, object] = {}
    count = len(prices)
    if count < min_bars:
        years = count / 252.0
        message = (
            f"History is only {count} bars (~{years:.1f} years). "
            f"For meaningful quant validation, use at least {min_bars} bars (~5 years)."
        )
        warnings_list.append(message)
        warnings.warn(message, stacklevel=2)
        helpful["suggested_data_source"] = "yfinance"
        helpful["suggested_lookback"] = "20y"
        helpful["suggested_tickers"] = ["SPY", "AAPL", "MSFT"]
        helpful["min_bars_warned"] = min_bars
        helpful["current_bars"] = count
    return {
        "warnings": warnings_list,
        "help": helpful,
        "short_history": count < min_bars,
    }


def load_yfinance(
    ticker: str,
    *,
    start: str | None = None,
    end: str | None = None,
    period: str = "3y",
    interval: str = "1d",
    auto_adjust: bool = True,
) -> list[float]:
    if not _YFINANCE_AVAILABLE:
        raise RuntimeError("yfinance is not installed in this environment")
    tk = yf.Ticker(ticker)
    if start or end:
        hist = tk.history(start=start, end=end, interval=interval, auto_adjust=auto_adjust)
    else:
        hist = tk.history(period=period, interval=interval, auto_adjust=auto_adjust)
    if hist is None or hist.empty:
        raise ValueError(f"No data returned for ticker {ticker!r}")
    closes = hist["Close"].dropna().astype(float).tolist()
    if len(closes) < 80:
        raise ValueError(f"Insufficient data for {ticker!r}: {len(closes)} rows")
    return closes


def _yfinance_download_one(ticker: str, start: str, end: str, interval: str, auto_adjust: bool) -> dict[str, Any]:
    tk = yf.Ticker(ticker)
    hist = tk.history(start=start, end=end, interval=interval, auto_adjust=auto_adjust)
    if hist is None or hist.empty:
        raise ValueError(f"No data returned for bulk ticker {ticker!r}")
    frame = hist.reset_index()
    frame["Date"] = pd.to_datetime(frame["Date"]).dt.tz_localize(None)
    row = {
        "ticker": ticker,
        "start": frame["Date"].min().isoformat(),
        "end": frame["Date"].max().isoformat(),
        "bars": int(len(frame)),
        "first_close": float(frame["Close"].iloc[0]),
        "last_close": float(frame["Close"].iloc[-1]),
        "dividend_adjusted": bool(auto_adjust),
        "splits": int(len(frame[frame["Stock Splits"] > 0])) if "Stock Splits" in frame.columns else 0,
        "dividends": int(len(frame[frame["Dividends"] > 0])) if "Dividends" in frame.columns else 0,
    }
    closes = hist["Close"].dropna().astype(float).tolist()
    split_series = hist.get("Stock Splits", pd.Series(dtype=float)).fillna(0.0).tolist() if _PANDAS_AVAILABLE else []
    dividend_series = hist.get("Dividends", pd.Series(dtype=float)).fillna(0.0).tolist() if _PANDAS_AVAILABLE else []
    return {
        "closes": closes,
        "meta": row,
        "splits": [float(v) for v in split_series if float(v) != 0.0],
        "dividends": [float(v) for v in dividend_series if float(v) != 0.0],
    }


def load_yfinance_bulk(
    tickers: list[str],
    *,
    start: str,
    end: str,
    interval: str = "1d",
    auto_adjust: bool = True,
    corporate_action_adjustment: bool = True,
) -> dict[str, Any]:
    if not _YFINANCE_AVAILABLE:
        raise RuntimeError("yfinance is not installed in this environment")
    if not _PANDAS_AVAILABLE:
        raise RuntimeError("pandas is required for yfinance bulk download")
    if not tickers:
        raise ValueError("tickers must not be empty")
    adjusted = auto_adjust and corporate_action_adjustment
    results: dict[str, Any] = {"tickers": {}, "universe_meta": []}
    for ticker in {t.upper().strip() for t in tickers if t.strip()}:
        try:
            payload = _yfinance_download_one(ticker, start, end, interval, adjusted)
            results["tickers"][ticker] = {
                "closes": payload["closes"],
                "meta": payload["meta"],
                "corporate_actions": {
                    "method": "auto_adjust" if adjusted else "raw",
                    "splits": payload["splits"],
                    "dividends": payload["dividends"],
                },
            }
            results["universe_meta"].append(payload["meta"])
        except Exception as exc:
            logger.warning("yfinance bulk ticker failed: %s: %s", ticker, exc)
            results["tickers"][ticker] = {"error": str(exc)}
    return results


def load_fred_series(
    series_ids: list[str],
    *,
    start: str,
    end: str | None = None,
    api_key: str | None = None,
) -> dict[str, list[float | None]]:
    if not series_ids:
        return {}
    if not _REQUESTS_AVAILABLE:
        raise RuntimeError("requests is required for FRED fetcher")
    end = end or __import__("datetime").date.today().isoformat()
    base = "https://api.stlouisfed.org/fred/series/observations"
    params: dict[str, Any] = {
        "series_id": ",".join({s.strip() for s in series_ids if s.strip()}),
        "observation_start": start,
        "observation_end": end,
        "file_type": "json",
        "frequency": "d",
        "output_type": 1,
        "sort_order": "asc",
    }
    if api_key:
        params["api_key"] = api_key
    resp = requests.get(base, params=params, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    series_payload = payload if isinstance(payload, list) else payload.get("observations", [])
    keyed: dict[str, list[float | None]] = {sid: [] for sid in series_ids}
    for item in series_payload:
        sid = item.get("series_id")
        val = item.get("value")
        if sid not in keyed or val in (None, ".", ""):
            if sid in keyed:
                keyed[sid].append(None)
            continue
        try:
            keyed[sid].append(float(val))
        except (TypeError, ValueError):
            keyed[sid].append(None)
    return keyed


_VALID_FRED_SERIES = {
    "GDP": "Gross Domestic Product",
    "CPIAUCSL": "Consumer Price Index",
    "UNRATE": "Unemployment Rate",
    "VIXCLS": "CBOE Volatility Index",
    "DGS10": "10-Year Treasury Constant Maturity Rate",
    "DGS2": "2-Year Treasury Constant Maturity Rate",
}


def default_fred_series() -> list[str]:
    return list(_VALID_FRED_SERIES.keys())


def fred_series_descriptions() -> dict[str, str]:
    return dict(_VALID_FRED_SERIES)
