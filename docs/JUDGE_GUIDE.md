# Judge guide

Market Fuzzer is intentionally evaluated through one complete developer workflow, not through the older calibration console.

## Fastest local path

From the repository root:

```bash
make install
make judge-demo
```

Open the printed local URL and follow:

1. **Start with POV example**.
2. Review the fragile strategy and the safety properties.
3. Run the normal baseline; it should show `PASS`.
4. Click **Break My Strategy**.
5. Open the participation failure and its minimized conditions.
6. Open **Replay** and jump to the first violation.
7. Run the exact same scenario with corrected POV.
8. Export the YAML/JSON regression fixture.
9. Run the printed `smw test ...` command.

The no-key path is the reference path. It uses the real deterministic harness and never loads precomputed screenshots or staged results.

## Docker path

```bash
docker compose up --build
```

The service is available at <http://127.0.0.1:8000>. Verify it with:

```bash
curl -fsS http://127.0.0.1:8000/api/health
```

The compose service mounts only `./artifacts` for generated fixtures. It does not require an OpenAI API key. Add `OPENAI_API_KEY` only to the server environment if you want to test the optional analyst action; never put that key in the browser or repository.

## What the result means

The deterministic result says that the selected strategy violated a declared safety property in the displayed bounded synthetic scenario. It does not say that the strategy will fail in a real venue, that the market model is institutionally calibrated, or that the strategy is unprofitable or unsafe for live trading.

The GPT-5.6 panel is interpretation only. The evidence package and local grounding checks prevent it from changing the measured verdict. If no key is configured, the panel explicitly says `DETERMINISTIC FALLBACK · NO API KEY`.

## Troubleshooting

- If port 8000 is occupied, run `PORT=8010 make judge-demo`.
- If a prior process is running, stop it before starting the judge script.
- If Docker is unavailable, use the Python path; both exercise the same no-key application.
- If the browser shows old results after changing a strategy or property, use the reset/restart action; the current UI also clears stale result state automatically.

## Verification command

```bash
make verify
```

This runs formatting, Ruff, mypy, the complete pytest suite, determinism, provenance, demo smoke, and `git diff --check`.
