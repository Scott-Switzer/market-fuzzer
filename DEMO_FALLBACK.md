# demo.py — fallback & reproduction notes

## 2026-07-21 run notes
- Python requirement in `pyproject.toml` is `>=3.12`, but the bundled `.venv` has
  Python 3.11.3. This caused `zip(..., strict=True)` in `app/simulation.py`
  to raise `TypeError`, blocking any demo execution until patched.
- Same run threatened to hit the earlier 180s timeout on `yfinance` 20-year
  downloads; extended runs should be allowed up to 10–20 minutes.

### Data fallback chain
1. `yfinance.Ticker.history(start=..., end=..., interval="1d", auto_adjust=True, timeout=30)`
2. Project helper `app.break_test.data_loader.load_yfinance(...)`.
3. Direct Yahoo HTTP download via `query1.finance.yahoo.com/v7/finance/download/...`.
4. Deterministic GBM fallback prices with documented seed basis
   `demo_20y_v1`, persisted to `data/yfinance_fallback/<TICKER>.csv`.

Use `data/yfinance_fallback/<TICKER>.csv` to reproduce runs without network.

### Required Python environment
- Use a Python 3.12+ interpreter if possible.
- Install dependencies:
  ```
  python3.12 -m venv .venv312
  source .venv312/bin/activate
  python -m pip install -U pip setuptools wheel
  python -m pip install -r <project requirements>
  ```
  If virtualenv packaging in this shell environment breaks `.venv312` again,
  use `python3.12 -m pip install --user` into the global Python 3.12 site.
