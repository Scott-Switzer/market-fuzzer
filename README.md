# Synthetic Market World Engine

> A calibrated counterfactual market-validation platform for execution stress testing and governed synthetic-data release.

Historical backtests replay the market that occurred. Synthetic Market World tests how a generated market responds to the strategy itself.

The commercial surface has three modules: **Execution Validation**, **Counterfactual Research**, and **Governed Data Release**.

## Product workflow

1. **Calibrate:** compile a user-acquired canonical sample into an aggregate-only `CalibrationPackV1`, or use the deterministic no-data demo pack.
2. **Intervene:** reduce displayed depth, add a forced seller, and sweep participation from 2% to 20%.
3. **Validate:** run common-random-number worlds across an accepted calibration ensemble and gate every conclusion.
4. **Release:** export a `SyntheticMarketPackage` only with explicit fit-for-use and confidentiality/derivation evidence.

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
smw calibrate --mode quick
smw validate-market configs/presets/fragile-small-cap.yaml --mode quick
smw demo
```

## Architecture

The authoritative path is `WorldSpec → synthetic world → heterogeneous agents → latency queue → integer-tick price-time-priority exchange → metrics/artifacts`. GPT-5.6 is isolated to structured compilation and assumption disclosure. See [architecture](docs/ARCHITECTURE.md) and [methodology](docs/METHODOLOGY.md).

## GPT-5.6 role

Set `OPENAI_API_KEY` and optionally `OPENAI_MODEL` (default `gpt-5.6`) to use the online compiler. The current OpenAI SDK Responses structured-output path validates model output directly into Pydantic models. The model never produces prices, fills, accounting, or executable code. Offline mode supports the full demonstration.

## Reproducibility and artifacts

Every world has canonical JSON and a SHA-256 specification hash. Validation campaigns write orders, trades, book states, latent regimes, intervention labels, calibration evidence, separate simulator and release reports, manifests, and hashes under `artifacts/<experiment-id>/`. Source rows are never copied into an artifact package.

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

Use the no-key quick start, load the calibration pack, compile the default world, and run the 96-world quick campaign. The browser follows Calibration → Intervention → Validation → Governed Release.

## Limitations and safety

This prototype is not institutionally calibrated, does not prove strategy profitability or safety, and is not suitable for live trading without independent validation. It is research and testing infrastructure, not investment advice. See [limitations](docs/LIMITATIONS.md) and [roadmap](docs/ROADMAP.md).

## License

MIT. Research references and reuse boundaries are documented separately.

> We do not claim every synthetic market is realistic. We prove which decisions each market is fit to support.
