# Data Source Research for Synthetic Security Universe

Goal: assemble free/open-sourceable multi-asset historical and corporate-action data for a synthetic security universe (equities, ETFs, FX, futures-adjacent, macro). This file documents exact APIs/download patterns, schema, limits, licensing, and practical caveats. No massive datasets are downloaded here.

## 1) yfinance — multi-asset historical bars + corporate actions

- **Source repo/docs:** https://github.com/ranaroussi/yfinance, https://ranaroussi.github.io/yfinance/reference/index.html
- **License:** MIT/open-source library; data itself is Yahoo/Verizon Media public-facing data, not a commercial redistribution license. Treat as best-effort research data.
- **Auth/CORS:** No API key required. This is a Python client around Yahoo Finance public endpoints. In a browser/web-app context, Yahoo Finance endpoints are not an open CORS API; if serving via a local backend or desktop app, use yfinance server-side.
- **Asset classes:** US and many global equities, ETFs, mutual funds, indexes, FX pairs, crypto, some futures. Coverage depends on Yahoo Finance ticker availability.
- **How to fetch bars:**
  - `yf.Ticker(ticker).history(period="2y", interval="1d")` returns `Date, Open, High, Low, Close, Volume, Dividends, Stock Splits`. Intervals: `1m/2m/5m/15m/30m/60m/90m/1h/1d/5d/1wk/1mo`.
  - `yf.download(["AAPL","MSFT"], period="2y", interval="1d")` for multi-ticker pulls.
  - `auto_adjust=True` returns split/dividend adjusted OHLC; default `auto_adjust=False` returns raw OHLC plus `Adj Close` in recent versions depending on yfinance version.
- **Corporate actions (splits/dividends):**
  - `ticker.splits` and `ticker.dividends` return `pandas.Series` indexed by date.
  - `history()` already includes `Dividends` and `Stock Splits` columns when events land on a trading day.
- **Schema:**
  - Columns: `datetime` index, `Open`, `High`, `Low`, `Close`, `Volume`, `Dividends`, `Stock Splits`.
  - Timezone-aware datetime index, usually exchange tz.
- **Caveats:**
  - Unofficial wrapper around Yahoo; endpoints change, and rate limiting/captchas can happen under heavy use.
  - Intraday history is limited-ish; for deep history prefer Alpha Vantage month-by-month or Stooq dumps.
  - For adjusted-price pipelines, explicitly handle `auto_adjust`/`Adj Close` depending on version; the repo README has migration notes.

## 2) Stooq — bulk CSV bars (no API)

- **Home:** https://stooq.com/db/
- **License:** Free for personal/non-commercial use. Explicitly states “personal use” on download pages.
- **Auth/CORS:** No API and no auth. Direct file download URLs. Browser automation/captcha may appear occasionally.
- **Asset classes:** Stocks (US, UK, JP, HK, PL, HU, etc.), ETFs, indices, FX/currencies, bonds, futures, options, cryptocurrencies, macro/rates. Very broad for a free source.
- **Download pattern (direct URLs):**
  - Base: `https://stooq.com/db/d/`
  - Files are zip/ASCII files organized by market and timeframe:
    - US daily: `https://stooq.com/db/d/?b=d_us_txt` (~513 MB)
    - World daily: `https://stooq.com/db/d/?b=d_world_txt` (~182 MB)
    - US hourly: `h_us_txt`, etc.
    - 5-minute: `5_us_txt`
  - Folder listings show available markets/timeframes.
- **Schema (ASCII):** Generally tab/comma-ish daily OHLCV: `Ticker, Date, Open, High, Low, Close, Volume, ...`. Not fully normalized across markets; inspect a small sample before bulk parsing.
- **Caveats:**
  - Not programmatic JSON; you parse ASCII/CSV.
  - Data is not split/dividend adjusted in Stooq files unless the file type states it. Adjust as needed or pair with another split/dividend source.
  - Large downloads; the task boundary says don’t fetch giant datasets, so document URLs but queue small samples first.

## 3) Twelve Data — REST API (free tier)

- **Docs:** https://twelvedata.com/docs
- **Auth:** Free API key required (`https://twelvedata.com/support/api-key`).
- **Free-tier limits:** Free Basic tier typically provides limited credits/min and daily quota; Docs Pricing page shows limits depend on plan. Use their documented limits per endpoint. Many community summaries cite roughly 800 requests/day and ~3 credits/min on the free tier, but treat the docs as source of truth if integrating seriously.
- **Asset classes:** Equities/ETFs, FX/forex, crypto, indices, commodities, fixed income/bonds. Also technical indicators, fundamentals, dividends/splits calendars, earnings/IPO calendars.
- **Endpoints relevant here:**
  - Time series: `/api/time-series`, `/api/time-series/cross`, `/api/real-time-price`
  - End-of-day: `/api/end-of-day-price`
  - Reference: `/stocks`, `/etfs`, `/forex-pairs`, `/cryptocurrencies-pairs`, `/markets`, `/exchange-schedule`
  - Corporate actions: `/api/dividends`, `/api/splits`, `/api/dividends-calendar`, `/api/splits-calendar`
- **Data formats:** JSON; several endpoints also support CSV.
- **CORS:** If calling from a browser, CORS is a typical consideration for any proprietary REST API; use a backend proxy when in doubt.
- **Caveats:**
  - Free-tier quotas are strict; batching by symbols/dates is required for backfilling.
  - Intraday and higher-rate histories require paid plans.

## 4) Alpha Vantage — REST API (free tier)

- **Docs:** https://www.alphavantage.co/documentation/
- **Auth:** Free API key via `https://www.alphavantage.co/support/#api-key`.
- **Free-tier limits:** 25 requests per day, 5 requests per minute on the free tier. Premium plans raise limits.
- **Endpoints relevant here:**
  - Daily adjusted bars: `function=TIME_SERIES_DAILY_ADJUSTED&symbol=IBM&outputsize=full&apikey=KEY`
  - Daily raw bars: `TIME_SERIES_DAILY`
  - Intraday historical by month: `TIME_SERIES_INTRADAY&interval=5min&month=YYYY-MM&outputsize=full` (historical intraday requires month-by-month pagination).
  - Dividends: `DIVIDENDS?symbol=IBM&apikey=KEY`
  - Splits: `SPLITS?symbol=IBM&apikey=KEY`
  - Forex/crypto/commodities/economic indicators also available.
- **Schema:**
  - Daily adjusted JSON keys: `1. open`, `2. high`, `3. low`, `4. close`, `5. adjusted close`, `6. volume`, `7. dividend amount`, `8. split coefficient`.
  - Split/dividend endpoints return date + ratio/amount arrays.
- **Asset classes:** Global equities, ETFs/indexes, FX, crypto, commodities.
- **Download pattern:**
  - Simple GET requests returning JSON/CSV. No SDK required.
  - Example: `https://www.alphavantage.co/query?function=TIME_SERIES_DAILY_ADJUSTED&symbol=IBM&outputsize=full&apikey=demo`
- **CORS:** Public API; if using from frontend, CORS may apply. Typical integration is via backend/python.
- **Caveats:**
  - 25/day means do not iterate over many symbols naively. Prefer a small symbol basket or use other sources for universe expansion.
  - Monthly intraday backfill is verbose and slow on free tier.

## 5) Polygon.io — REST API (free tier)

- **Docs/pricing:** https://polygon.io/pricing, https://massive.com/docs
- **Auth:** API key after sign-up.
- **Free tier:** Stocks Basic is $0/month with 5 calls/min, 2 years historical EOD/15min aggregates, no realtime, no WebSockets.
- **Asset classes:** US equities/stocks, options, indices, currencies, crypto. The free Stocks Basic plan covers equities; FX/crypto/futures typically require add-ons or other tiers.
- **Key endpoints:**
  - Aggregates: `/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}`
  - Previous close: `/v2/aggs/ticker/{ticker}/prev`
  - Corporate actions: REST corporate actions endpoints exist on higher tiers; free tier is mostly aggregates/reference.
- **CORS/Auth:** Header-based API key. For local backend use; avoid exposing key in client-side assets.
- **Caveats:**
  - Free tier leaves out realtime and deep history; for synthetic universe research it is useful for recent 2 years US equities at 15min/EOD.
  - Aggregate bars can be requested at day, minute, second on paid plans.

## 6) FRED (Federal Reserve Economic Data) — macro

- **Site/docs:** https://fred.stlouisfed.org/docs/api/fred/
- **Auth:** Free API key recommended but optional for some uses; register at https://fred.stlouisfed.org/docs/api/api_key.html.
- **Limits:** Free tier is generous; commonly cited around 1,000 requests/day and 120/min for API key users. Check the docs for exact current limits.
- **Asset classes / coverage:** Macroeconomic and financial series: rates, employment, inflation, GDP, exchange rates, commodity prices, financial conditions, yield curves, etc.
- **Endpoints:**
  - Search series: `fred/series/search?search_text=...`
  - Observations: `fred/series/observations?series_id=GS10&...`
- **Data schema:** Date + value arrays. Some series have units/transformation flags.
- **Useful series examples:**
  - VIX proxy-equivalent: `VIXCLS`
  - Treasury yields: `GS10`, `GS2`, `GS5`, `DGS3MO`
  - Fed Funds: `FEDFUNDS`
  - Broad macro: `UNRATE`, `CPIAUCSL`
- **CORS:** Official API is backend-friendly; no browser-CORS guarantee. Use a backend/data collector.
- **Caveats:**
  - Series are survey/agency published; vintage/revision handling matters for backtesting. Use ALFRED/vintage endpoints if needed.

## 7) Nasdaq Data Link (formerly Quandl) — some free datasets

- **Docs:** https://docs.data.nasdaq.com/docs/getting-started
- **Auth:** Free API key + `?api_key=...` parameter.
- **Coverage:** Mixed free/premium datasets. Free macro/commodity/economic datasets exist; most equity pricing/fundamentals are premium.
- **Known free datasets:**
  - CFTC Commitment of Traders (COT): `CFTC` — free tables API.
  - Some macro/financial datasets are free; browse https://data.nasdaq.com/search?free=true.
- **Data format:** CSV/JSON via REST, depending on dataset type.
- **CORS/Auth:** Standard key auth. Use backend; do not embed key in client-side code.
- **Caveats:**
  - Most equity price and fundamentals datasets moved behind premium.
  - Good for macro, futures positioning, and some public-interest macro series.

## 8) LOB / limit order book data

- **LOBSTER:** https://lobsterdata.com
  - Provides NASDAQ ITCH-based reconstructed order book data for academic/professional use.
  - Not offered as a free programmatic API with anonymous login; the normal path is a request/form-based access with academic restrictions.
  - Realistic option for research if you qualify for academic access; otherwise not open/no-key free in the sense of a simple public endpoint.
- **Other free-ish alternatives to consider:** Public crypto order books from exchange public APIs are more accessible. True equity LOB for US equities is mostly gated or requires Nasdaq ITCH bulk + own reconstructor.

## 9) SEC EDGAR — corporate actions/filings-derived data

- **Docs:** https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- **Auth:** None required; public APIs. Must follow SEC terms and rate limits/policy.
- **CORS:** `data.sec.gov` APIs do not support CORS. Use server-side only.
- **Endpoints:**
  - Submissions by CIK: `https://data.sec.gov/submissions/CIK##########.json`
  - XBRL company concept: `https://data.sec.gov/api/xbrl/companyconcept/CIK##########/us-gaap/StockholdersEquity.json`
  - XBRL company facts: `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
  - XBRL frames: `https://data.sec.gov/api/xbrl/frames/us-gaap/AccountsPayableCurrent/USD/CY2019Q1I.json`
  - Bulk zips: `submissions.zip`, `companyfacts.zip` refreshed nightly.
- **Corporate actions:** EDGAR does not emit a clean split/dividend event feed in one endpoint. For splits, inspect 8-K, 10-Q, 10-K filings or structured XBRL where available. For dividends, look at declared dividends or cash-flow financing disclosures in filings. This is more work than using yfinance/Alpha Vantage for splits/dividends.
- **Rate limits:** SEC recommends politeness limits; do not hammer the endpoint. Use nightly bulk ZIPs when possible.

## 10) Additional free macro / rates / volatility

- **CBOE VIX historical CSV:** https://www.cboe.com/tradable_products/vix/vix_historical_data — downloadable CSV files for daily VIX closing values. Explicit public data product.
- **U.S. Treasury rates:** https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rate-archives — CSV/XML/ZIP archives. Daily Treasury Par Yield Curve, bill rates, etc. Free, public.
- **FRED (see above):** Best maintained macro API; includes rates, spreads, and market-implied macro proxies.

## 11) Quick cross-source priority matrix

| Priority | Source | Strengths | Limitations |
|---|---|---|---|
| 1 | yfinance | Multi-asset bars, splits, dividends, indexes, FX, crypto | Unofficial Yahoo wrapper, rate limit/captcha risk |
| 2 | Stooq | No-key bulk CSV, many markets/timeframes, huge coverage | No API, no adjustments, personal-use license |
| 2 | Twelve Data | Dedicated API, corporate actions, ETFs/FX/crypto/commodities | Free tier strict quota |
| 2 | Alpha Vantage | Adjusted bars + splits/dividends + macro + FX | 25 req/day free |
| 2 | Polygon free | EOD/15min for US equities 2y | 5 req/min |
| 2 | FRED | Macro/rates/volatility | Need symbol knowledge |
| 3 | Nasdaq Data Link | Free datasets like CFTC COT | Most equity data is premium |
| 3 | LOBSTER | High-fidelity NASDAQ LOB | Request/access-gated |
| 4 | SEC EDGAR | Official filings, XBRL, no auth | No direct split/dividend feed; no CORS |
| 5 | CBOE/Treasury | VIX and yield-curve CSV downloads | Manual/browse-style retrieval |

## 12) Practical recommendations for this repo

1. **Primary bar source:** Start with `yfinance` for equities/ETFs/indexes/FX/crypto because one client handles tickers, splits, dividends, and `history()` schema. Treat it as research-grade data, not settlement-grade.
2. **Adjusted close coverage:** If `auto_adjust=False`, explicitly use `Adj Close` or `TIME_SERIES_DAILY_ADJUSTED` from Alpha Vantage to source/validate splits/dividends.
3. **Macro overlays:** Add FRED for rates and VIX (`VIXCLS`) and Treasury yields. This lets the synthetic universe align return series with risk-free / macro state variables.
4. **Bulk expansion or index replication:** Use Stooq for broader equity coverage when ticker-level Yahoo coverage is thin.
5. **Order book / microstructure:** Expect friction. LOBSTER is likely gated. For synthetic break testing, OHLCV plus macro and corporate actions may be enough.
6. **SEC EDGAR:** Use as a fallback for corporate action truth if yfinance is missing splits/dividends for obscure tickers; prefer backend-only due to CORS block.
7. **Auth discipline:** Store every vendor key in environment config; document required env vars if we ever add live fetchers.

## 13) Offline price-cache CSV schema for multi-asset synthetic generation

Use this schema when expanding the synthetic universe with cached local price subsets or free datasets like yfinance/Stooq exports. The offline path keeps generation deterministic without live network calls.

- **File:** UTF-8 CSV/TSV. Blank lines and lines starting with `#` are ignored.
- **Row format:** one ticker per row, prices ordered oldest → newest.
- **Required header row:** `ticker` followed by at least one price column. Header columns after `ticker` do not need names, but you can use `close_1...close_N` for readability.
- **Price rules:** positive finite floats only. If a row has fewer than 20 prices, it is skipped for cache use. Missing values should be removed before export; gaps should be forward-filled or dropped.
- **Length alignment:** generator re-centers each cached series to `initial_price_ticks` by dividing by the first cached price in the selected window, then multiplying by the asset’s initial ticks. Cached length can be longer than the simulation length; the generator uses the latest `length` values.
- **Recommended naming:** `ticker_close_history_YYYYMMDD.csv`. Example:

```
# Offline price cache for factor-loading correlated generation.
# One ticker per row, comma-separated price values aligned to latest trading session.
# Example schema: ticker,close_1,close_2,...,close_N
AAPL,173.50,174.05,173.80,174.25,175.10,174.80,175.45,176.00,175.60,176.25
MSFT,420.10,421.80,420.90,422.30,423.15,422.80,423.95,424.70,424.20,425.10
GOOGL,140.20,141.10,140.90,141.55,142.30,142.05,142.80,143.20,142.95,143.50
```

- **Helper:** `ResearchSyntheticMarketGenerator.create_sample_price_cache(path, universe)` writes a commented example file.
- **Loader:** `ResearchSyntheticMarketGenerator.load_price_cache(path)` returns `dict[str, list[float]]` keyed by ticker.
- **Usage in code:** pass `price_cache=ResearchSyntheticMarketGenerator.load_price_cache(path)` and `use_price_cache=True` into `generate_correlated_paths` or `generate_one_factor_asset_path`.
- **Known limitation:** cache rows only provide price shape, not corporate actions or splits. Adjust cached inputs upstream before saving.
