.PHONY: install install-browser verify test e2e demo run run-example arena-demo decision-benchmark regression judge-demo docker-smoke performance clean-artifacts

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

install:
	$(PYTHON) -m pip install -e '.[dev]'

install-browser:
	$(PYTHON) -m playwright install chromium

test:
	$(PYTHON) -m pytest

e2e:
	$(PYTHON) scripts/browser_e2e.py

verify:
	$(PYTHON) -m ruff format --check app scripts tests
	$(PYTHON) -m ruff check app scripts tests
	$(PYTHON) -m mypy app
	$(PYTHON) -m pytest
	$(PYTHON) scripts/determinism_check.py
	$(PYTHON) scripts/provenance_check.py
	$(PYTHON) scripts/demo_smoke.py
	$(PYTHON) scripts/arena_smoke.py
	$(PYTHON) scripts/browser_e2e.py
	bash -n scripts/judge_demo.sh
	node --check app/static/app.js
	node --check app/static/arena.js
	git diff --check

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
