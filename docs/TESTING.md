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

The product tests cover the protected Market Fuzzer workflow plus Quant Challenge Arena determinism, public/hidden separation, strict CSV validation, structural-break/delay/cost/false-feature metrics, public-versus-robust ranking reversal, structured GPT boundaries, role-scoped API responses, and feedback grounding.

## Judge path

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, choose **Instructor**, approve the seeded Arena challenge, bundle public data, run the two included examples, then switch to **Student** to load a practice submission, validate it, submit it, and inspect the public leaderboard. Release hidden results from the instructor console to show the robustness ranking reversal. No API key or financial-data subscription is required.

The protected Market Fuzzer workflow remains at `http://127.0.0.1:8000/market-fuzzer`: choose **Start with POV example**, run the baseline, break the strategy, open replay, retest corrected POV, export a fixture, and run the regression suite.

## CLI smoke path

```bash
.venv/bin/python -m app.cli test artifacts/market_fuzzer/failure_<id>.yaml
.venv/bin/python -m app.cli test artifacts/market_fuzzer/failure_<id>.json
```

The broader synthetic-world and calibration commands remain available, but they are secondary research infrastructure rather than the primary Arena or Market Fuzzer path.
