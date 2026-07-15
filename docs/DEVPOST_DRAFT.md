# Devpost draft

## Tagline

Find the market conditions that break your trading algorithm before the market does.

## Short description

Market Fuzzer is a Developer Tools product that runs execution strategies against bounded synthetic market conditions, finds a reproducible safety-property violation, minimizes the counterexample, and exports it as a regression test.

## Full write-up

Historical backtests show one realized market path. Market Fuzzer gives a quant developer a different workflow: choose a built-in POV strategy, define what safe behavior means, run a normal baseline, search adverse but deterministic conditions, inspect the failure replay, retest a corrected implementation on the exact same scenario, and preserve the result as a fixture.

The Build Week product slice is deliberately a compact deterministic market test harness. It models discrete steps, actual and delayed observed volume, latency, pending orders, fills, parent-order remainder, realized participation, completion, a deterministic shortfall proxy, and replay evidence. The fragile POV intentionally ignores pending orders and stale volume contraction; the corrected POV applies a pending-order budget and fill-time participation guard. The search targets participation and never lets GPT decide a verdict.

## Built with

Python, FastAPI, Pydantic, NumPy, Typer, OpenAI Responses API/GPT-5.6 (optional), Codex, HTML, CSS, JavaScript, Pytest, Ruff, Mypy, and GitHub Actions.

## Installation and judge testing

Follow `docs/TESTING.md`. The offline browser workflow needs no API key. Start the POV tutorial, run baseline, break the strategy, inspect replay, retest corrected POV, export a fixture, and run the regression suite.

## GPT-5.6 and Codex

GPT-5.6 may produce schema-constrained failure hypotheses and explanations grounded in measured simulation evidence. It never determines prices, fills, property values, reproduction confidence, or PASS/FAIL. Codex implemented and repaired the deterministic harness, search/minimization workflow, fixture contract, API, CLI, tests, and product UI. Add the final `/feedback` session ID before submission.

## Limitations

The product proves software behavior only within its declared compact synthetic environment. It is not institutional calibration, a production capacity estimate, a profitability claim, a live-trading recommendation, or regulatory approval. The repository’s broader synthetic-world research modules are retained as secondary infrastructure and are not silently represented as the Market Fuzzer backend.

## License

MIT. No proprietary market data or secrets are committed.
