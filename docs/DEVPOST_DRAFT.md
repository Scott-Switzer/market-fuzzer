# Devpost draft

## Tagline

A programmable synthetic exchange that stress-tests quant strategies in realistic counterfactual markets before they reach production.

## Short description

Synthetic Market World turns a natural-language market scenario into a validated, reproducible world of fictional issuers, information events, heterogeneous agents, and exact exchange mechanics. Quant teams can deploy an execution strategy, observe endogenous effects, and compare failure behavior across controlled worlds.

## Full write-up

Historical backtests expose a strategy to one realized history. Replay also cannot fully answer how liquidity providers and other participants would respond when the strategy changes its size or timing. Synthetic Market World provides a controlled counterfactual laboratory.

A researcher describes a market, inspects the explicit typed specification and assumptions, and runs three synthetic companies on a continuous double-auction exchange. Market makers, fundamental, momentum, mean-reversion, noise, forced-liquidation, and execution agents interact through price-time-priority books with integer ticks, partial fills, cancellations, fees, latency, ledgers, and halts. The same strategy then runs through normal, liquidity-withdrawal, earnings-shock, and crowded-unwind worlds using common seeds.

Results include execution metrics, scenario-by-participation failure surfaces, component realism diagnostics, and a self-contained audit bundle with specifications, Parquet logs, hashes, and reproduction commands.

## Built with

Python, FastAPI, Pydantic, NumPy, Pandas, PyArrow, Typer, OpenAI Responses API, GPT-5.6, Codex, HTML, CSS, JavaScript, Pytest, Hypothesis, Ruff, Mypy, Docker, GitHub Actions.

## Installation and judge testing

Use the commands in `docs/TESTING.md`. The complete offline workflow needs no API key. Open the browser app, compile the default world, run the exchange, configure a strategy, and run the counterfactual battery.

## GPT-5.6

GPT-5.6 uses structured output to compile natural-language world requests into the same strict schema as offline presets. It identifies assumptions and warnings. It never sets individual prices, orders, fills, accounting entries, or measured outcomes.

## Codex

Codex implemented, tested, repaired, and documented the product; coordinated license, architecture, and UX research; added deterministic and invariant checks; and produced the release and submission material. Add the final `/feedback` session ID before submission.

## Limitations and attribution

The prototype is not calibrated to institutional order-book data and is not suitable for live trading. ABIDES, JAX-LOB, TRADES/DeepMarket, and MarS were studied, but no source, weights, or datasets were copied. Exact revisions and licenses are in `THIRD_PARTY_NOTICES.md`.
# Market Fuzzer

Market Fuzzer is a developer product for discovering bounded synthetic market conditions that violate declared execution safety properties, minimizing the counterexample, and preserving it as a regression fixture.

