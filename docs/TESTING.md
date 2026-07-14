# Testing and judge instructions

## Complete verification

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
make verify
```

From the repository root, `make verify` runs formatting, lint, type checking, unit/integration tests, determinism, provenance, no-key demo smoke, and whitespace checks.

## Judge path

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, compile the default prompt in Offline mode, run the exchange, scrub through the event, configure the execution strategy, and run the 24-run battery. No API key or financial data subscription is needed.

Supported platforms: macOS, Linux, and Windows with Python 3.12+; Docker is also supported.
