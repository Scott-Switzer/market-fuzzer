# Synthetic Market World Engine

> A programmable synthetic exchange that stress-tests quant strategies in realistic counterfactual markets before they reach production.

Historical backtests replay the market that occurred. Synthetic Market World tests how a generated market responds to the strategy itself.

## Product workflow

1. Describe a counterfactual market in natural language.
2. Compile it offline or with GPT-5.6 into a strict, editable, hashed world specification.
3. Run three fictional issuers, a macro factor, information events, seven agent roles, and exact exchange mechanics.
4. Deploy TWAP or participation-of-volume execution into the market.
5. Compare four worlds at multiple participation rates and common seeds.
6. Inspect execution metrics, component realism diagnostics, a failure surface, and reproducible artifacts.

## Quick start — no key

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. No financial data or OpenAI API key is required.

## CLI

```bash
smw validate configs/presets/normal.yaml
smw compile --prompt "thin liquidity and an earnings shock" --offline
smw run configs/presets/normal.yaml
smw batch configs/presets/fragile-small-cap.yaml
smw demo
```

## Architecture

The authoritative path is `WorldSpec → synthetic world → heterogeneous agents → latency queue → integer-tick price-time-priority exchange → metrics/artifacts`. GPT-5.6 is isolated to structured compilation and assumption disclosure. See [architecture](docs/ARCHITECTURE.md) and [methodology](docs/METHODOLOGY.md).

## GPT-5.6 role

Set `OPENAI_API_KEY` and optionally `OPENAI_MODEL` (default `gpt-5.6`) to use the online compiler. The current OpenAI SDK Responses structured-output path validates model output directly into Pydantic models. The model never produces prices, fills, accounting, or executable code. Offline mode supports the full demonstration.

## Reproducibility and artifacts

Every world has canonical JSON and a SHA-256 specification hash. Same-seed outputs have a separate deterministic result hash. Batch experiments write JSON, YAML, Parquet logs, reports, artifact hashes, code commit, seeds, mutations, package context, and reproduction commands under `artifacts/<experiment-id>/`.

## Testing

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/make verify
```

This covers formatting, lint, typing, unit/integration tests, matching invariants, determinism, artifact verification, provenance, and no-key demo smoke. See [testing](docs/TESTING.md).

## Build Week and Codex

The original engine and product were written during OpenAI Build Week. [Hackathon work](docs/HACKATHON_WORK.md) separates new work, inspiration, and deferred integrations. [Codex collaboration](docs/CODEX_COLLABORATION.md) describes implementation and review. Add the final `/feedback` session ID before submission.

## Third-party systems

ABIDES, ABIDES-JPMC, JAX-LOB, DeepMarket/TRADES, and MarS were inspected at pinned revisions. No code, model weight, checkpoint, or proprietary dataset was copied. See [third-party notices](THIRD_PARTY_NOTICES.md).

## Judge path

Use the no-key quick start, compile the default prompt, run Exchange Lab, configure the strategy, and run the 24-run battery. The browser exposes measured results, diagnostics, limitations, hashes, and downloads without rebuilding research models.

## Limitations and safety

This prototype is not institutionally calibrated, does not prove strategy profitability or safety, and is not suitable for live trading without independent validation. It is research and testing infrastructure, not investment advice. See [limitations](docs/LIMITATIONS.md) and [roadmap](docs/ROADMAP.md).

## License

MIT. Research references and reuse boundaries are documented separately.

> Do not test tomorrow’s strategy only against yesterday’s market.
