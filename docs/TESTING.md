# Testing and judge instructions

## Complete verification

```bash
.venv/bin/python -m ruff format --check app tests
.venv/bin/python -m ruff check app tests
.venv/bin/python -m mypy app
.venv/bin/python -m pytest -q
.venv/bin/python scripts/determinism_check.py
.venv/bin/python scripts/provenance_check.py
.venv/bin/python scripts/demo_smoke.py
git diff --check
make verify
```

The product tests cover baseline PASS, targeted participation failure, deterministic reproduction, minimized evidence, verified neighbor PASS, exact corrected comparison, YAML/JSON export, CLI replay, invalid/mismatched fixtures, and regression-suite execution.

## Judge path

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, choose **Start with POV example**, inspect the strategy and safety properties, run the baseline, click **Break My Strategy**, open replay, retest corrected POV, export a fixture, and run the regression suite. No API key or financial-data subscription is required.

## CLI smoke path

```bash
.venv/bin/python -m app.cli test artifacts/market_fuzzer/failure_<id>.yaml
.venv/bin/python -m app.cli test artifacts/market_fuzzer/failure_<id>.json
```

The broader synthetic-world and calibration commands remain available, but they are secondary research infrastructure rather than the primary Market Fuzzer path.
