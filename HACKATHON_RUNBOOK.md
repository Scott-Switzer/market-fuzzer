# Break Test — Hackathon Runbook

## Product
Break Test stresses a strategy across history plus synthetic exchange-forward regimes so you can see where it wins, where it bleeds, and what to change.

## Demo path
### 1. Install dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run the 30-second demo script
```bash
env -u PYTHONPATH .venv/bin/python demo.py
```

### 3. Expected output highlights
- 20-year yfinance history load for AAPL/MSFT/GOOGL.
- Historical SMA crossover backtest: total return, max drawdown, Sharpe, current trade signal.
- 4 forward-test regimes with 10 worlds each: Steady Trend, Sideways & Choppy, High Volatility, Sudden Selloff.
- Per-regime world stats: completed worlds, loss rate, median return, best return, worst drawdown.
- Failure summary and plain-English alternatives.
- Reported wall time for the run.

### 4. Files to show
- `demo.py`
- `app/break_test/exchange_fwd.py`
- `app/break_test/synthetic_market.py`
- `app/simulation.py`
- `app/break_test/report_export.py`
- `app/break_test/service.py`

## Talking points for judges / firms
- **Real data + synthetic stress:** starts from 20 years of live closes, then forward-tests on a deterministic synthetic exchange with latency, halts, and regime shocks.
- **8-asset universe:** demo uses an expanded universe preset with AAPL plus 7 synthetic-related assets.
- **Plain-English failure report:** outputs what failed, why, and parameter alternatives.
- **Deterministic seeds:** every world is reproducible by regime key and seed.
- **Operational fit:** script is idempotent, runs offline without keys, and returns a single summary block.

## Known limitations
- Forward-test path uses a lightweight regime/stress model; it is not a live brokerage simulator.
- PDF export is a placeholder.
- Default demo uses 10 worlds/regime so the script stays under 60 seconds on a modern laptop; raise `WORLDS_PER_REGIME` for deeper analysis.
