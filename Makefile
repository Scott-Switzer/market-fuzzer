.PHONY: install install-browser verify test e2e demo run run-example arena-demo decision-benchmark regression judge-demo docker-smoke performance clean-artifacts verify-submission test-portfolio-engine test-data-adapters test-strategy-identity submission-demo pitch-deck fenrix-inspect

# Default to the project Python 3.12 virtualenv if present,
# otherwise fall back to whatever `python3` resolves to.
PYTHON ?= $(firstword $(wildcard .venv312/bin/python .venv/bin/python) python3)

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

# --- Fenrix Submission MVP targets ---
test-portfolio-engine:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m pytest tests/submission/test_portfolio_engine.py -q -p no:cacheprovider --tb=short

test-data-adapters:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m pytest tests/submission/test_data_adapters.py -q -p no:cacheprovider --tb=short

test-strategy-identity:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m pytest tests/submission/test_strategy_identity.py -q -p no:cacheprovider --tb=short

verify-submission:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m pytest tests/submission -q -p no:cacheprovider --tb=short
	$(PYTHON) scripts/submission_verify.py

submission-demo:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m app.strategy_lab.submission.cli demo

pitch-deck:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m app.strategy_lab.submission.cli build-deck

fenrix-inspect:
	env -u PYTHONPATH PYTHONNOUSERSITE=1 $(PYTHON) -m app.strategy_lab.data inspect-fenrix
