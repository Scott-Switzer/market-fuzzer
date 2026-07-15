# Five-minute judge guide

The primary path is Quant Challenge Arena at `/`. Market Fuzzer is an advanced lab at `/market-fuzzer`; the older research/positions challenge is not part of this five-minute walkthrough.

## Launch

Fastest isolated path after `make install`:

```bash
make judge-demo
```

The command prints the URL and temporary local instructor code. It removes isolated state when stopped unless `JUDGE_KEEP_ARTIFACTS=1`.

Equivalent explicit launch:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
ARENA_DEMO_AUTH=1 \
ARENA_DEMO_INSTRUCTOR_CODE=change-this-local-demo-code \
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. No API key or market-data subscription is required.

Docker alternative:

```bash
ARENA_DEMO_AUTH=1 \
ARENA_DEMO_INSTRUCTOR_CODE=change-this-local-demo-code \
docker compose up --build
```

## 0:00–1:20 — Student practice

1. Click **Student demo** to obtain a signed local demo session with a server-generated student ID. Clicking it again with the valid role cookie resumes the same identity rather than resetting its practice/submission state.
2. Select **Aggressive POV**. The form remains editable; built-ins are presets, not the only submissions.
3. Review the policy summary and click **Run public practice**.
4. Inspect public completion, implementation shortfall, and the synchronized market/strategy replay. The evidence table should show child submissions, fill quantity, remaining parent quantity, per-step participation, and shortfall contribution.
5. Click **Submit final policy**. The UI records one immutable final policy and reports the submission ID.

What this proves: participant input is strict declarative data, the public world is derived server-side at the exact public seed `42`, and the visible result comes from the exchange rather than GPT.

## 1:20–3:10 — Instructor evaluation

1. Enter the local code from `ARENA_DEMO_INSTRUCTOR_CODE`, then click **Instructor demo**.
2. Click **Lock submissions**. Public practice and final submission close.
3. Click **Evaluate protected matrix**. This may take several seconds because all benchmark and submitted policies run across the persisted hidden-world manifest and protected `SEEDS = (41, 42)`.
4. Click **Release allowed results**, then **Refresh rankings**.
5. Compare the public and robustness columns:

```text
Aggressive POV       public rank 1 → lower robustness rank
Guarded Adaptive POV lower public rank → robustness rank 1
```

The exact score values and matrix hash are rendered from the current verified run. Do not use values from screenshots or an older commit as acceptance evidence.

What this proves: only a server-generated instructor identity that presented the server code can advance phases; hidden worlds came from the persisted manifest; the evaluation was stored before release; and release atomically changes visibility, phase, and audit state without recomputing scores.

## 3:10–4:15 — Evidence and GPT-5.6 boundary

1. Choose Aggressive and Guarded in the comparison view on the same world hash and seed.
2. Point to different order/fill decisions, participation path, inventory path, impact, and latency timestamps.
3. Click **Explain evidence**.

Without an API key, the source label must say deterministic fallback/no key. With a configured key, the GPT-5.6 response must cite valid evidence IDs. The package contains release-safe overall/intent aggregates and stable public-trace IDs, not protected seed rows, internal world IDs, hashes, or replays. Request the same report again to confirm the persisted report is recovered rather than regenerated. In both cases deterministic score and rank remain unchanged.

What this proves: the ranking reversal is traceable to measured engine behavior, and GPT is an educational analyst rather than the simulation or scoring authority.

## 4:15–5:00 — Reproducibility and Market Fuzzer

1. Show the matrix hash and persisted phase.
2. Restart the app if time permits; the released challenge and submission remain in SQLite.
3. Open **Advanced Market Fuzzer**. Confirm `/market-fuzzer` loads the protected strategy-failure workflow.

What this proves: the final assessment is reproducible and restart-safe, and the earlier milestone remains operational.

## Expected verified result

The production default uses the seed list and scoring version displayed in the UI’s reproducibility evidence. Acceptance requires:

- Aggressive POV public rank is `1`;
- Guarded Adaptive POV robustness rank is `1`;
- the two policies use the same world/seed/parent-order basis in comparison;
- the student hidden route is denied before release;
- released student rows omit raw `world_results` and world hashes;
- instructor evidence remains protected after release; and
- feedback source is explicit.

See [PERFORMANCE.md](PERFORMANCE.md) for the reproducible probe and current refresh status, and [EXECUTION_CHALLENGE.md](EXECUTION_CHALLENGE.md) for actual metric definitions.

## Troubleshooting

- Port occupied: use `--port 8010` and open `http://127.0.0.1:8010`.
- Old state: set `ARENA_DB_PATH=/tmp/quant-arena-judge.sqlite3` before launch, or remove only that explicitly chosen temporary file.
- Instructor session fails: restart with `ARENA_DEMO_AUTH=1`, set an `ARENA_DEMO_INSTRUCTOR_CODE` of at least eight characters, and enter that same code in the UI.
- Hidden action returns 409: follow the phase order—lock, evaluate, release.
- No GPT key: expected; the deterministic fallback is the complete reference path.
- Verify the build: `make verify && make docker-smoke`.

## Honest claim

This walkthrough proves a deterministic educational assessment inside the declared fictional exchange. It does not prove real-market calibration, profitability, best execution, production capacity, or live-trading safety.
