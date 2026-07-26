.PHONY: install install-browser verify verify-fast test e2e demo run run-example arena-demo decision-benchmark regression judge-demo docker-smoke performance clean-artifacts verify-submission test-portfolio-engine test-data-adapters test-strategy-identity submission-demo pitch-deck fenrix-inspect render-smoke lock lock-check test-postgres

# Default to the project Python 3.12 virtualenv if present,
# otherwise fall back to whatever `python3` resolves to.
PYTHON ?= $(firstword $(wildcard .venv312/bin/python .venv/bin/python) python3)

# Single documented lock command. Regenerates requirements.lock (hashed) from
# pyproject.toml's runtime + dev dependency graph. Do NOT hand-edit the lock or
# use `pip freeze` -- the lock must describe the same graph as pyproject.
lock:
	$(PYTHON) -m piptools compile \
	  --extra dev \
	  --resolver backtracking \
	  --generate-hashes \
	  --strip-extras \
	  --output-file requirements.lock \
	  pyproject.toml

# CI consistency gate: verify the committed lock satisfies every pyproject
# dependency specifier. Stable against transient upstream releases (unlike a
# raw `pip-compile | git diff`, which is flaky against live PyPI).
lock-check:
	$(PYTHON) scripts/lock_consistency_check.py

# Real-PostgreSQL verification (reset brief section 11/12). Requires
# FENRIX_TEST_POSTGRES_URL + FENRIX_DATABASE_URL pointing at a live Postgres.
test-postgres:
	$(PYTHON) -m alembic upgrade head
	$(PYTHON) -m alembic check
	$(PYTHON) -m pytest tests/integration/test_postgres_persistence.py tests/integration/test_migrations.py -q -p no:cacheprovider


install:
	$(PYTHON) -m pip install -e '.[dev]'

install-browser:
	$(PYTHON) -m playwright install chromium

test:
	$(PYTHON) -m pytest

e2e:
	$(PYTHON) scripts/browser_e2e.py

verify:
	$(PYTHON) -m ruff format --check app scripts tests docs
	$(PYTHON) -m ruff check app scripts tests docs
	$(PYTHON) -m mypy app/strategy_lab
	$(MAKE) verify-strategy-lab
	$(PYTHON) -m pytest
	$(PYTHON) scripts/determinism_check.py
	$(PYTHON) scripts/provenance_check.py
	$(PYTHON) scripts/demo_smoke.py
	$(PYTHON) scripts/arena_smoke.py
	$(PYTHON) scripts/browser_e2e.py
	@test -f scripts/judge_demo.sh && bash -n scripts/judge_demo.sh || true
	@test -f app/static/app.js && node --check app/static/app.js || true
	@test -f app/static/arena.js && node --check app/static/arena.js || true
	git diff --check

# Fast local loop: skips the slow Playwright browser_e2e + arena_smoke serial
# suites and the determinism/provenance scripts. Use for quick iteration; the
# full `make verify` (CI) still runs everything.
verify-fast:
	$(PYTHON) -m ruff format --check app scripts tests docs
	$(PYTHON) -m ruff check app scripts tests docs
	$(PYTHON) -m mypy app/strategy_lab
	$(MAKE) verify-strategy-lab
	$(PYTHON) -m pytest -q -p no:cacheprovider
	$(PYTHON) scripts/demo_smoke.py
	git diff --check

verify-strategy-lab:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m pytest tests/strategy_lab -q -p no:cacheprovider --tb=short
	@test -f app/static/strategy-lab.html || { echo 'missing app/static/strategy-lab.html'; exit 1; }

run:
	$(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port 8000

demo:
	$(PYTHON) -m app.cli demo --serve

run-example:
	$(PYTHON) -m app.cli run-example

arena-demo:
	$(PYTHON) scripts/arena_smoke.py

# Clean deploy smoke test: install ONLY the Render dependency set (no -e . dev
# editable) and confirm the app imports + boots, mirroring the Render build.
render-smoke:
	$(PYTHON) -m pip install -r requirements-render.txt
	$(PYTHON) -c "import app.main; print('render import ok')"

decision-benchmark:
	$(PYTHON) scripts/decision_benchmark_smoke.py

regression:
	$(PYTHON) -m app.cli test artifacts/market_fuzzer

judge-demo:
	./scripts/judge_demo.sh

docker-smoke:
	@set -eu; \
	$(PYTHON) scripts/docker_preflight.py; \
	export GIT_COMMIT_SHA=$$(git rev-parse HEAD); \
	export ARENA_PORT=$${ARENA_DOCKER_PORT:-18080}; \
	project=quant-arena-smoke; \
	cleanup() { docker compose -p $$project down --volumes --remove-orphans >/dev/null 2>&1 || true; }; \
	trap cleanup EXIT INT TERM; \
	cleanup; \
	docker compose -p $$project build --quiet; \
	docker compose -p $$project up -d --wait --wait-timeout 90; \
	ARENA_BASE_URL=http://127.0.0.1:$$ARENA_PORT $(PYTHON) scripts/docker_health_smoke.py; \
	$(PYTHON) scripts/load_smoke.py --base-url http://127.0.0.1:$$ARENA_PORT

performance:
	$(PYTHON) scripts/performance_probe.py

clean-artifacts:
	rm -rf artifacts/smw-*

# --- Fenrix Submission Final-Hardening targets ---
# The pitch deck MUST use the real yfinance historical run of record.
# If no cached yfinance data exists, the historical target FAILS (refuses synthetic).
submission-demo-historical:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m app.strategy_lab.submission.cli demo --mode historical

submission-demo-offline:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m app.strategy_lab.submission.cli demo --mode synthetic_fixture

test-portfolio-engine:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m pytest tests/submission/test_portfolio_engine.py -q -p no:cacheprovider --tb=short

test-portfolio-accounting:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m pytest tests/submission/test_portfolio_accounting.py -q -p no:cacheprovider --tb=short

test-execution-timing:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m pytest tests/submission/test_execution_timing.py -q -p no:cacheprovider --tb=short

test-stress-mechanisms:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m pytest tests/submission/test_stress_mechanisms.py -q -p no:cacheprovider --tb=short

test-failure-confirmation:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m pytest tests/submission/test_failure_confirmation.py -q -p no:cacheprovider --tb=short

test-deck-evidence:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m pytest tests/submission_audit/test_deck_evidence.py -q -p no:cacheprovider --tb=short

verify-submission:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m pytest tests/submission -q -p no:cacheprovider --tb=short
	$(PYTHON) scripts/submission_verify.py

# Deck uses historical evidence; if historical acquisition fails, the deck target fails.
pitch-deck: submission-demo-historical
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m app.strategy_lab.submission.cli build-deck

fenrix-inspect:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m app.strategy_lab.data inspect-fenrix
