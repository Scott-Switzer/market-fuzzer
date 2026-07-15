# Market Fuzzer

Market Fuzzer finds the market conditions that break a trading algorithm, reduces each failure to a reproducible counterexample, and turns it into a regression test.

The current product slice is intentionally narrow and truthful: a deterministic POV execution harness with a fragile tutorial implementation and a corrected implementation. It tests explicit safety properties inside a bounded synthetic market search space. It does not forecast markets, prove alpha, estimate production capacity, or validate live trading.

## The workflow

```text
Strategy → Safety Properties → Baseline → Break My Strategy
→ Minimized Counterexample → Replay → Corrected Retest → Regression Fixture
```

The first-run tutorial uses a fragile POV strategy whose delayed-volume accounting and pending-order handling can violate a participation cap. The search targets that property specifically; it does not accept an unrelated completion failure. The corrected POV receives the exact same minimized market, parent-order parameters, safety properties, and seeds.

## Quick start — no key

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>, choose **Start with POV example**, run the baseline, click **Break My Strategy**, open replay, retest corrected POV, and export the regression fixture. No financial data subscription or OpenAI API key is required.

For a judge-style isolated launch that generates fresh artifacts and prints the exact test path:

```bash
make judge-demo
```

The script uses a temporary artifact root and removes it when stopped. Set `JUDGE_KEEP_ARTIFACTS=1` to retain the generated package. The same no-key flow can run in Docker:

```bash
docker compose up --build
```

The container listens on <http://127.0.0.1:8000>, runs as a non-root user, writes only to the mounted `artifacts/` directory, and exposes `/api/health`. A public deployment is not included in this repository.

## CLI

```bash
smw test path/to/fixture.yaml
smw test path/to/fixture.json
smw test artifacts/market_fuzzer
smw run-example
```

`smw test` validates schema version, scenario hash, strategy identity, safety properties, seeds, and expected outcomes. It loads the exact stored strategy ID and exits nonzero for mismatches or invalid fixtures. A directory may contain YAML and JSON fixtures; invalid legacy fixtures are reported explicitly.

The repository also retains the broader synthetic-market research engine and calibration commands:

```bash
smw validate configs/presets/normal.yaml
smw compile --prompt "thin liquidity and an earnings shock" --offline
smw demo
```

Those are secondary research infrastructure, not the primary Market Fuzzer product path.

## Architecture truth

The product path is a **compact deterministic market test harness**, not a claim of full exact exchange integration. `app/product.py` owns the POV state machine, delayed observations, pending orders, fills, participation, completion, shortfall proxy, replay timeline, search, minimization, comparison, and fixture export. The older `app/exchange/` and synthetic-world modules remain available as research infrastructure and are not silently represented as the product harness.

The deterministic evaluator is the accounting authority for every displayed product result. GPT-5.6, when configured, may propose structured failure hypotheses and explain verified evidence; it never determines prices, fills, property values, reproduction confidence, or PASS/FAIL outcomes. Offline deterministic hypotheses support the complete workflow.

The **Explain failure with GPT-5.6** action sends a small evidence package containing only the measured failure, minimized scenario, passing neighbor, reproduction records, and permitted evidence-reference IDs. Structured output is validated locally; unknown evidence references and unsupported numeric claims are rejected. Without `OPENAI_API_KEY`, the UI shows a clearly labeled deterministic fallback rather than pretending that GPT ran.

## What is tested

- Strategy correctness: valid quantities, participation, completion, and state handling.
- Execution robustness: bounded liquidity, latency, forced-flow, and volume-contraction conditions.
- Safety properties: completion, shortfall, participation, halt behavior, and remaining inventory.
- Regression resistance: exact YAML/JSON fixtures and CLI replay.

The result is a software-testing conclusion within the declared synthetic environment. It is not a production capacity estimate, market prediction, profitability claim, live-trading recommendation, or regulatory approval.

## Testing

```bash
.venv/bin/python -m ruff format --check app tests
.venv/bin/python -m ruff check app tests
.venv/bin/python -m mypy app
.venv/bin/python -m pytest -q
make verify
```

See [judge instructions](docs/JUDGE_GUIDE.md), [testing and judge instructions](docs/TESTING.md), [product workflow](docs/PRODUCT_WORKFLOW.md), [fixture contract](docs/REGRESSION_FIXTURES.md), [performance notes](docs/PERFORMANCE.md), and [limitations](docs/LIMITATIONS.md).

## Build Week and provenance

The repository contains the pre-existing research engine plus the Build Week Market Fuzzer product layer. [Hackathon work](docs/HACKATHON_WORK.md) and [Codex collaboration](docs/CODEX_COLLABORATION.md) separate those lanes. Add the final `/feedback` session ID before submission.

## License and safety

MIT. No proprietary market data or secrets are committed. This is research and testing infrastructure, not investment advice.

> Find the market conditions that break your trading algorithm before the market does.
