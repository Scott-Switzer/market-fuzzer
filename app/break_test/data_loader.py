from __future__ import annotations

import hashlib
import json
import logging
import warnings
import zipfile
from pathlib import Path
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
    requests = None  # type: ignore[import]
    _REQUESTS_AVAILABLE = False

try:
    from datetime import date, timedelta
except Exception:  # pragma: no cover
    date = None  # type: ignore[assignment,misc]
    timedelta = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

_MIN_BARS = 252 * 5
_DEFAULT_FENRIX_ZIP = Path("~/Documents/scott-brain/22_Fenrix/anonymized_bundle.zip").expanduser()
_FENRIX_LOADER_CACHE: dict[str, dict[str, list[float]]] = {}


def _is_date(value: object) -> bool:
    return (
        hasattr(value, "isoformat")
        and hasattr(value, "year")
        and hasattr(value, "month")
        and hasattr(value, "day")
    )


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


def load_fenrix(zip_path: str | Path | None = None) -> dict[str, list[float]]:
    """Return price histories from the Fenrix anonymized bundle.

    Loads ``market/price_series.csv`` from each ``COMPANY_XXX`` in the zip and
    returns a mapping ``{ticker: closes}``. Missing/invalid tickers are skipped.
    """
    path = Path(zip_path) if zip_path is not None else _DEFAULT_FENRIX_ZIP
    path = path.expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Fenrix anonymized bundle not found: {path}")
    cache_key = str(path)
    if cache_key not in _FENRIX_LOADER_CACHE:
        out: dict[str, list[float]] = {}
        try:
            with zipfile.ZipFile(path, mode="r") as bundle:
                for name in bundle.namelist():
                    parts = name.split("/")
                    if (
                        len(parts) >= 5
                        and parts[0] == "public"
                        and parts[1] == "anonymized"
                        and parts[3] == "market"
                        and parts[-1] == "price_series.csv"
                    ):
                        company_id = parts[2]
                        try:
                            raw = bundle.read(name).decode("utf-8", errors="ignore")
                        except Exception:
                            continue
                        lines = [
                            line.strip()
                            for line in raw.splitlines()
                            if line.strip() and not line.strip().startswith("#")
                        ]
                        if not lines:
                            continue
                        closes: list[float] = []
                        for line in lines[1:]:
                            cols = line.split(",")
                            if len(cols) < 2:
                                continue
                            try:
                                closes.append(float(cols[1]))
                            except (TypeError, ValueError):
                                continue
                        if closes:
                            out[company_id] = closes
        except zipfile.BadZipFile as exc:
            raise ValueError(f"Fenrix bundle is not a valid zip: {path}") from exc
        if not out:
            raise ValueError(f"No price histories found in Fenrix bundle: {path}")
        _FENRIX_LOADER_CACHE[cache_key] = out
    return _FENRIX_LOADER_CACHE[cache_key]


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


def _yfinance_download_one(
    ticker: str, start: str, end: str, interval: str, auto_adjust: bool
) -> dict[str, Any]:
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
    split_series = (
        hist.get("Stock Splits", pd.Series(dtype=float)).fillna(0.0).tolist() if _PANDAS_AVAILABLE else []
    )
    dividend_series = (
        hist.get("Dividends", pd.Series(dtype=float)).fillna(0.0).tolist() if _PANDAS_AVAILABLE else []
    )
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


def load_yfinance_daily_bulk(
    tickers: list[str],
    *,
    start: str | None = None,
    end: str | None = None,
    lookback: str = "20y",
    interval: str = "1d",
    auto_adjust: bool = True,
    cache_dir: str | Path | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    if not _YFINANCE_AVAILABLE:
        raise RuntimeError("yfinance is not installed in this environment")
    if not _PANDAS_AVAILABLE:
        raise RuntimeError("pandas is required for yfinance bulk download")
    if not tickers:
        raise ValueError("tickers must not be empty")

    cache_root = Path(cache_dir) if cache_dir is not None else Path("artifacts") / "yfinance_bulk"
    cache_root.mkdir(parents=True, exist_ok=True)

    today = date.today()
    end_date = today if end is None else date.fromisoformat(end)
    start_date = end_date - _parse_lookback(lookback) if start is None else date.fromisoformat(start)
    start_str = start_date.isoformat()
    end_str = end_date.isoformat()

    cache_key = hashlib.sha256(
        json.dumps(
            {
                "tickers": sorted({t.upper().strip() for t in tickers if t.strip()}),
                "start": start_str,
                "end": end_str,
                "interval": interval,
                "auto_adjust": auto_adjust,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:16]

    cache_path = cache_root / f"daily_bulk_{cache_key}.json"
    if cache_path.exists() and not force_refresh:
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cached = json.load(fh)
            if isinstance(cached, dict) and "tickers" in cached:
                return cached
        except Exception:
            pass

    unique_tickers = list({t.upper().strip() for t in tickers if t.strip()})
    if not unique_tickers:
        raise ValueError("tickers must not be empty after deduplication")

    results: dict[str, Any] = {
        "tickers": {},
        "universe_meta": [],
        "quality_summary": {
            "missing_tickers": [],
            "gaps": [],
            "corporate_actions": [],
            "survivorship_flags": [],
        },
    }
    batch_size = 50
    for batch_start in range(0, len(unique_tickers), batch_size):
        batch = unique_tickers[batch_start : batch_start + batch_size]
        if not batch:
            break

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                data = yf.download(
                    batch,
                    start=start_str,
                    end=end_str,
                    interval=interval,
                    auto_adjust=auto_adjust,
                    progress=False,
                    group_by="ticker",
                )

            if data is None or data.empty:
                continue

            for ticker in batch:
                try:
                    if isinstance(data.columns, pd.MultiIndex):
                        ticker_data = data[ticker] if ticker in data.columns.get_level_values(0) else None
                    else:
                        ticker_data = data if len(batch) == 1 else None

                    if ticker_data is None or ticker_data.empty:
                        if len(batch) == 1 and not data.empty:
                            ticker_data = data
                        else:
                            logger.warning("No data returned for bulk ticker %s", ticker)
                            results["tickers"][ticker] = {"error": "No data returned"}
                            results["quality_summary"]["missing_tickers"].append(ticker)
                            continue

                    closes = (
                        ticker_data["Close"].dropna().astype(float).tolist()
                        if "Close" in ticker_data.columns
                        else []
                    )
                    if not closes:
                        logger.warning("Empty closes for bulk ticker %s", ticker)
                        results["tickers"][ticker] = {"error": "Empty price series"}
                        results["quality_summary"]["missing_tickers"].append(ticker)
                        continue

                    dates = ticker_data.index
                    if hasattr(dates, "tz") and dates.tz is not None:
                        dates = dates.tz_localize(None)
                    date_strs = [pd.to_datetime(d).isoformat() for d in dates]

                    split_series = (
                        ticker_data.get("Stock Splits", pd.Series(dtype=float, index=ticker_data.index))
                        .fillna(0.0)
                        .tolist()
                    )
                    dividend_series = (
                        ticker_data.get("Dividends", pd.Series(dtype=float, index=ticker_data.index))
                        .fillna(0.0)
                        .tolist()
                    )

                    gaps = _detect_gaps(date_strs, max_gap_days=5)
                    corporate_actions = _detect_corporate_actions(split_series, dividend_series, date_strs)
                    survivorship_flag = _flag_survivorship_bias(closes, ticker)

                    quality = {
                        "gaps": gaps,
                        "corporate_actions": corporate_actions,
                        "survivorship_bias_flag": survivorship_flag,
                        "bar_count": len(closes),
                        "date_range": {
                            "start": date_strs[0] if date_strs else None,
                            "end": date_strs[-1] if date_strs else None,
                        },
                    }

                    if gaps:
                        results["quality_summary"]["gaps"].append({"ticker": ticker, "gaps": gaps})
                        warnings.warn(f"Ticker {ticker} has {len(gaps)} gaps > 5 days", stacklevel=2)

                    if corporate_actions["has_splits"] or corporate_actions["has_dividends"]:
                        results["quality_summary"]["corporate_actions"].append(
                            {"ticker": ticker, "actions": corporate_actions}
                        )
                        if corporate_actions["has_splits"]:
                            warnings.warn(f"Ticker {ticker} has stock splits in history", stacklevel=2)
                        if corporate_actions["has_dividends"]:
                            warnings.warn(f"Ticker {ticker} has dividends in history", stacklevel=2)

                    if survivorship_flag["flagged"]:
                        results["quality_summary"]["survivorship_flags"].append(
                            {"ticker": ticker, "details": survivorship_flag}
                        )
                        warnings.warn(
                            f"Ticker {ticker} flagged for survivorship bias: {survivorship_flag['reason']}",
                            stacklevel=2,
                        )

                    meta = {
                        "ticker": ticker,
                        "start": date_strs[0] if date_strs else start_str,
                        "end": date_strs[-1] if date_strs else end_str,
                        "bars": len(closes),
                        "first_close": closes[0] if closes else None,
                        "last_close": closes[-1] if closes else None,
                        "dividend_adjusted": bool(auto_adjust),
                        "splits": int(sum(1 for v in split_series if float(v) != 0.0)),
                        "dividends": int(sum(1 for v in dividend_series if float(v) != 0.0)),
                    }

                    results["tickers"][ticker] = {
                        "closes": closes,
                        "meta": meta,
                        "quality": quality,
                        "corporate_actions": {
                            "method": "auto_adjust" if auto_adjust else "raw",
                            "splits": [float(v) for v in split_series if float(v) != 0.0],
                            "dividends": [float(v) for v in dividend_series if float(v) != 0.0],
                            "gap_count": len(gaps),
                            "survivorship_bias": survivorship_flag,
                        },
                    }
                    results["universe_meta"].append(meta)

                except Exception as exc:
                    logger.warning("yfinance bulk ticker failed: %s: %s", ticker, exc)
                    results["tickers"][ticker] = {"error": str(exc)}
                    results["quality_summary"]["missing_tickers"].append(ticker)

        except Exception as exc:
            logger.error("yfinance bulk batch failed: %s", exc)
            for ticker in batch:
                results["tickers"][ticker] = {"error": f"Batch download failed: {exc}"}
                results["quality_summary"]["missing_tickers"].append(ticker)

    try:
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(results, fh, default=str)
    except Exception:
        pass

    return results


def _parse_lookback(lookback: str) -> timedelta:
    lookback = str(lookback).lower().strip()
    if lookback.endswith("y"):
        years = int(lookback[:-1])
        return timedelta(days=years * 365)
    if lookback.endswith("m"):
        months = int(lookback[:-1])
        return timedelta(days=months * 30)
    if lookback.endswith("d"):
        days = int(lookback[:-1])
        return timedelta(days=days)
    return timedelta(days=5 * 365)


def _detect_gaps(dates: list[str], max_gap_days: int = 5) -> list[dict[str, object]]:
    if len(dates) < 2:
        return []
    gaps = []
    try:
        parsed = [date.fromisoformat(d[:10]) for d in dates if d]
        for i in range(1, len(parsed)):
            delta = (parsed[i] - parsed[i - 1]).days
            if delta > max_gap_days:
                gaps.append({"from": parsed[i - 1].isoformat(), "to": parsed[i].isoformat(), "days": delta})
    except Exception:
        pass
    return gaps


def _detect_corporate_actions(
    splits: list[float], dividends: list[float], dates: list[str]
) -> dict[str, object]:
    has_splits = any(float(v) != 0.0 for v in splits)
    has_dividends = any(float(v) != 0.0 for v in dividends)
    return {
        "has_splits": has_splits,
        "has_dividends": has_dividends,
        "split_dates": [dates[i] for i, v in enumerate(splits) if float(v) != 0.0]
        if len(splits) == len(dates)
        else [],
        "dividend_dates": [dates[i] for i, v in enumerate(dividends) if float(v) != 0.0]
        if len(dividends) == len(dates)
        else [],
    }


def _flag_survivorship_bias(closes: list[float], ticker: str) -> dict[str, object]:
    if len(closes) < 252:
        return {"flagged": False, "reason": "insufficient_history"}
    returns = np.diff(np.log(np.asarray(closes, dtype=float)))
    if returns.size == 0:
        return {"flagged": False, "reason": "no_returns"}
    negative_return_count = int(np.sum(returns < -0.10))
    zero_return_count = int(np.sum(np.abs(returns) < 1e-10))
    if zero_return_count > len(returns) * 0.05:
        return {
            "flagged": True,
            "reason": "excess_flat_periods_suggest_delisting_or_merger",
            "zero_return_pct": zero_return_count / len(returns),
        }
    if negative_return_count == 0 and len(returns) > 1000:
        return {"flagged": True, "reason": "no_large_negative_returns_over_long_history"}
    return {"flagged": False, "reason": "clean"}


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
    end = end or date.today().isoformat()
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
