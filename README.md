# Counterfactual Markets

> A programmable synthetic exchange that stress-tests quant strategies in realistic counterfactual markets before they reach production.

This Build Week MVP compiles a natural-language market description into a seeded world specification and runs an execution strategy through normal, liquidity-withdrawal, earnings-shock, and crowded-unwind worlds.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## Test

```bash
python3 -m pytest -q
```

## MVP boundary

This is a deterministic, aggregate-depth prototype. It demonstrates prompt-to-spec compilation, reproducible scenario runs, execution impact, and a failure indicator. It is not calibrated to historical data and is not suitable for trading decisions.

## Build Week provenance

See [docs/HACKATHON_WORK.md](docs/HACKATHON_WORK.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

