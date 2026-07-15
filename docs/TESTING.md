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

Open `http://127.0.0.1:8000`, load the calibration pack, compile the default prompt, and run the 96-world quick validation campaign. Inspect the five validation vectors, claim gate, and governed release manifest. No API key or financial data subscription is needed.

Supported platforms: macOS, Linux, and Windows with Python 3.12+; Docker is also supported.
# Product checks

Run `pytest -q`, `ruff check app tests`, and `smw test path/to/fixture.yaml`. The product test demonstrates that fragile POV passes baseline but produces a reproducible bounded failure.
