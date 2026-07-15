.PHONY: install verify test demo run run-example regression judge-demo docker-smoke clean-artifacts

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

install:
	$(PYTHON) -m pip install -e '.[dev]'

test:
	$(PYTHON) -m pytest

verify:
	$(PYTHON) -m ruff format --check app tests
	$(PYTHON) -m ruff check app tests
	$(PYTHON) -m mypy app
	$(PYTHON) -m pytest
	$(PYTHON) scripts/determinism_check.py
	$(PYTHON) scripts/provenance_check.py
	$(PYTHON) scripts/demo_smoke.py
	bash -n scripts/judge_demo.sh
	node --check app/static/app.js
	git diff --check

run:
	$(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port 8000

demo:
	$(PYTHON) -m app.cli demo --serve

run-example:
	$(PYTHON) -m app.cli run-example

regression:
	$(PYTHON) -m app.cli test artifacts/market_fuzzer

judge-demo:
	./scripts/judge_demo.sh

docker-smoke:
	docker compose build --quiet
	docker compose up -d
	@trap 'docker compose down' EXIT; \
	for i in $$(seq 1 30); do \
		$(PYTHON) -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=1)" && exit 0; \
		sleep 1; \
	done; \
	docker compose logs; exit 1

clean-artifacts:
	rm -rf artifacts/smw-*
