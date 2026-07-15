.PHONY: install verify test demo run run-example regression clean-artifacts

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
	git diff --check

run:
	$(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port 8000

demo:
	$(PYTHON) -m app.cli demo --serve

run-example:
	$(PYTHON) -m app.cli run-example

regression:
	$(PYTHON) -m app.cli test artifacts/market_fuzzer

clean-artifacts:
	rm -rf artifacts/smw-*
