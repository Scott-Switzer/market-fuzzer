# Quant Challenge Arena

Quant Challenge Arena is an Education-track platform where students submit quantitative strategies to a public synthetic market and are evaluated against hidden, deterministic regimes. It teaches the difference between a leaderboard backtest and evidence that generalizes.

The current challenge, **When the Backtest Winner Loses**, is intentionally small and truthful: three fictional assets, a public momentum regime, and hidden structural-break, one-day-delay, liquidity-shock, and false-feature tests. The deterministic engine owns data generation, validation, scoring, integrity checks, and ranking. GPT-5.6 may generate challenge content and grounded feedback, but never scores or ranks a submission.

## Arena workflow

```text
Instructor generates → Approves public panel → Student submits CSV
→ Deterministic public score → Instructor reveals hidden regimes
→ Robustness-adjusted ranking → Grounded feedback
```

Open <http://127.0.0.1:8000> after starting the server. The primary UI is the Arena. Use the instructor console to approve the seeded challenge, bundle the public data, run the two included strategy fixtures, and release the hidden results. Switch to Student to validate or submit a strict `date,asset,position` CSV.

The public leaderboard is deliberately allowed to disagree with the hidden robustness ranking. Example A is designed to win publicly and collapse in hidden regimes; Example B is designed to rank lower publicly and win on robustness.

## Secondary developer tool: Market Fuzzer

The protected Market Fuzzer milestone remains available at <http://127.0.0.1:8000/market-fuzzer>. It finds bounded synthetic conditions that break an execution strategy, minimizes the failure, replays it, and exports a regression fixture.

```text
Strategy → Safety Properties → Baseline → Break My Strategy
→ Minimized Counterexample → Replay → Corrected Retest → Regression Fixture
```

The first-run Market Fuzzer tutorial uses a fragile POV strategy whose delayed-volume accounting and pending-order handling can violate a participation cap. The search targets that property specifically; it does not accept an unrelated completion failure. The corrected POV receives the exact same minimized market, parent-order parameters, safety properties, and seeds.

## Quick start — no key

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>, choose **Instructor**, and follow the Arena workflow. No financial data subscription or OpenAI API key is required. The deterministic no-key path is complete; an OpenAI key only adds optional structured challenge prose and feedback.

To exercise the secondary Market Fuzzer, open `/market-fuzzer`, choose **Start with POV example**, run the baseline, click **Break My Strategy**, open replay, retest corrected POV, and export the regression fixture.

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

Those are secondary research infrastructure, not the primary Arena or Market Fuzzer product path.

## Arena architecture truth

`app/arena.py` owns the challenge schema, deterministic regime engine, strict CSV contract, public/hidden dataset separation, metrics, integrity checks, scoring, and feedback grounding. Hidden rows and regime manifests are generated server-side and are never returned by student-facing endpoints. The API uses `X-Role: instructor` for instructor-only operations in this prototype; production deployment would replace that demo boundary with authenticated course roles.

The scoring contract keeps public performance, hidden performance, regime stability, operational robustness, concentration, and explanation quality separate. GPT-5.6 is constrained to challenge generation and evidence-grounded feedback. It never determines scores, ranks, verdicts, prices, or hidden data.

The browser intentionally leads with a student/instructor product flow rather than raw JSON. Technical evidence, hashes, manifests, and the hidden bundle are available only in the instructor console or advanced evidence drawer.

## Market Fuzzer architecture truth

The product path is a **compact deterministic market test harness**, not a claim of full exact exchange integration. `app/product.py` owns the POV state machine, delayed observations, pending orders, fills, participation, completion, shortfall proxy, replay timeline, search, minimization, comparison, and fixture export. The older `app/exchange/` and synthetic-world modules remain available as research infrastructure and are not silently represented as the product harness.

The deterministic evaluator is the accounting authority for every displayed product result. GPT-5.6, when configured, may propose structured failure hypotheses and explain verified evidence; it never determines prices, fills, property values, reproduction confidence, or PASS/FAIL outcomes. Offline deterministic hypotheses support the complete workflow.

The **Explain failure with GPT-5.6** action sends a small evidence package containing only the measured failure, minimized scenario, passing neighbor, reproduction records, and permitted evidence-reference IDs. Structured output is validated locally; unknown evidence references and unsupported numeric claims are rejected. Without `OPENAI_API_KEY`, the UI shows a clearly labeled deterministic fallback rather than pretending that GPT ran.

## What Arena tests

- Public performance versus hidden performance.
- Structural-break and false-feature generalization.
- One-day signal-delay sensitivity.
- Transaction-cost and liquidity-shock sensitivity.
- Exposure concentration and simple integrity indicators.
- Reproducibility: same challenge seed and CSV produce the same metrics and hashes.
- Submission contract: complete public dates, known assets, bounded positions, and exposure limits.

The Arena result is a classroom assessment inside a declared fictional market. It does not prove alpha, detect misconduct, or predict future markets.

## What Market Fuzzer tests

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

The repository contains a protected pre-existing Market Fuzzer milestone plus the new Quant Challenge Arena product layer. [Hackathon work](docs/HACKATHON_WORK.md), [provenance](docs/BUILD_WEEK_PROVENANCE.md), and [integration ADR](docs/decisions/ADR_QUANT_CHALLENGE_ARENA_INTEGRATION.md) separate those lanes. Add the final `/feedback` session ID before submission.

## License and safety

MIT. No proprietary market data or secrets are committed. This is research and testing infrastructure, not investment advice.

> Find the market conditions that break your trading algorithm before the market does.
