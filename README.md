# Synthetic Market World

**A governed synthetic market environment for strategy validation and adversarial stress testing.**

Quant Challenge Arena is the training and assessment surface built on the
platform. Open the enterprise entry point at `/synthetic-market-world`.

**Problem.** A strategy can top a visible backtest by exploiting one friendly market and still fail when liquidity disappears, latency rises, order flow crowds, or an event changes the price path.

**Product.** Synthetic Market World lets a research or trading team register reproducible synthetic market worlds, apply controlled stress scenarios, run strategies against protected conditions, and produce governed evidence packages. Quant Challenge Arena is the learner-facing demonstration of that platform. Deterministic code owns market events, orders, fills, metrics, scores, ranks, phase state, and release state.

**Audience.** The primary users are prop-shop quant researchers, trading technology teams, and model-validation practitioners. Training, recruiting, and education are supported workflows. It evaluates strategy behavior inside declared synthetic markets; it does not prove alpha, production capacity, or live-trading safety.

## Three-step demo

1. Select **Aggressive POV**, edit the permitted policy controls, and run public practice.
2. Save the final declarative policy; an instructor locks submissions and runs the protected evaluation matrix.
3. Release the result and compare **public rank → robustness rank**. In the production default seed matrix, Aggressive POV wins the visible ranking while Guarded Adaptive POV wins the robustness ranking. Replay evidence connects that reversal to measured orders, fills, participation, inventory, impact, and latency—not a hardcoded policy label.

The central reveal is simple:

> A strategy can win the visible practice leaderboard but lose after hidden robustness testing.

## Launch

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
ARENA_DEMO_AUTH=1 \
ARENA_DEMO_INSTRUCTOR_CODE=change-this-local-demo-code \
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. `ARENA_DEMO_AUTH=1` enables deliberately scoped demo sessions. The server generates the student or instructor identity; the browser does not submit a user ID. Re-selecting a role with its still-valid role cookie resumes that persisted identity, including its practice count and saved submission. The instructor role additionally requires the server-configured local code entered in the UI. Choose a different code for your run. The code is compared server-side and is never returned in the session payload. When `ARENA_SESSION_SECRET` is omitted in demo mode, the server generates a process-random secret: reloads work, but cookies intentionally stop validating after a process restart. Set a stable secret of at least 32 bytes only when restart continuity is needed.

Cookies are `Secure` by default. The direct HTTP loopback launch above is detected narrowly and may omit `Secure`; `ARENA_COOKIE_SECURE=1` forces it. `ARENA_COOKIE_SECURE=0` is valid only with `ARENA_DEMO_AUTH=1` and is effective only for the same verified loopback/test scope—non-loopback clients still receive `Secure` cookies. Any other override value fails closed. This is not institutional authentication. No market-data subscription or OpenAI API key is required.

After installing dependencies, the one-command isolated judge launch is:

```bash
make judge-demo
```

It prints the URL and local instructor code, stores SQLite and generated artifacts in a temporary directory, and removes them when stopped unless `JUDGE_KEEP_ARTIFACTS=1`.

Docker is the one-command isolated path:

```bash
ARENA_DEMO_AUTH=1 \
ARENA_DEMO_INSTRUCTOR_CODE=change-this-local-demo-code \
docker compose up --build
```

The image runs as a non-root user, persists Arena state and Market Fuzzer artifacts in a named volume, and exposes a semantic health check at `/api/health`. Demo authentication is off by default; Compose passes the instructor code from the host environment and does not bake it into the image.

For the enterprise research-appliance path, set `ARENA_ENTERPRISE_API_KEY` and
`ARENA_ADAPTER_ALLOWED_HOSTS` before starting Compose. The World Registry can
attach aggregate-only customer calibration evidence; the Strategy Stress Lab
can compile a plain-English brief or register an executable `http_json_v1`
adapter. See [`docs/OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md) and
[`docs/DATA_AND_SIMULATION_ARCHITECTURE.md`](docs/DATA_AND_SIMULATION_ARCHITECTURE.md)
for the supported boundary and local-data workflow.

## Product map

```text
Primary        Execution Challenge Arena (/ and /api/arena/execution/...)
Secondary      Research/positions challenge (/api/arena/challenges/...)
Advanced       Market Fuzzer (/market-fuzzer)
Infrastructure Synthetic world, agents, and price-time-priority exchange
```

The protected Market Fuzzer milestone remains intact at <http://127.0.0.1:8000/market-fuzzer>. It searches bounded synthetic conditions, minimizes a counterexample, replays the failure, compares a corrected POV implementation, and exports a regression fixture. The older research/positions challenge remains available through its API but is explicitly experimental and is not the homepage.

## Student and instructor boundary

The public challenge returns a protected-test count, public policy definitions, practice limits, and the visible world narrative. It does not return hidden identifiers, parameters, hashes, replays, or raw evidence.

The lifecycle is explicit and persisted in SQLite:

```text
draft → public_practice → submission_locked → hidden_evaluation → released → archived
```

Only an instructor demo session created with the configured instructor code can lock, evaluate, or release. Normal `X-Role` headers are ignored. The `ARENA_TEST_AUTH=1` bypass is additionally gated to Starlette's in-process `testclient` request scope, so setting the environment variable cannot make its role headers authoritative for a real network client. Public practice uses the stored public world and the exact public seed `42`. Protected evaluation reads the persisted hidden-world manifest and the internal protected seed tuple `SEEDS = (41, 42)`; neither the manifest nor those seeds are accepted from a learner request. Release changes visibility, not the immutable stored evaluation. Instructor-only raw world evidence and audit history remain separately protected.

Phase changes, practice/final quota checks plus writes, evaluation persistence, release state, and their audit records are committed transactionally in SQLite. The quota count and insert run under the same immediate write transaction, so concurrent requests cannot both pass a stale limit check in the supported single-database demo.

Participant input is a strict versioned policy object, never uploaded Python. The schema supports `twap`, `pov`, and `adaptive_pov` plus bounded participation, spread, urgency, latency-tolerance, cancel, completion-buffer, and pause controls. Extra fields and incompatible combinations are rejected. Built-in policies go through the same engine adapter as student policies.

## Deterministic evidence and GPT-5.6

The exchange and scoring pipeline is authoritative. GPT-5.6 has two bounded roles:

- produce an instructor-only, schema-constrained **qualitative draft** that is persisted for review and maps only to approved intervention intents; and
- explain an already-verified released evidence package using only valid metric and evidence IDs.

Challenge-design output cannot contain numeric market parameters, seeds, outcomes, scores, or ranks and cannot create or mutate a world. The instructor UI exposes this as a clearly labeled qualitative draft. Feedback receives release-safe overall and educational-intent aggregates plus bounded, stable IDs derived from the learner's own public replay; raw hidden world rows remain instructor-only. Model output is strict structured data. Unknown evidence IDs, invented numbers, hidden references before release, investment advice, and attempts to change deterministic rank or score are rejected. Refusals and incomplete output are handled explicitly. The validated feedback report is persisted and returned on later requests or after restart instead of being regenerated. Without `OPENAI_API_KEY`, the complete workflow shows a labeled deterministic explanation template; it never presents fallback text as model-generated.

## Verification

```bash
make install
make install-browser
make verify
make docker-smoke
```

`make verify` runs formatting, lint, typing, pytest, determinism and provenance checks, both offline smoke paths, JavaScript syntax checks, and the headless Chromium lifecycle test. The E2E test covers student practice/submission, pre-release hidden denial, instructor lock/evaluate/release, ranking reversal, released feedback, a clean browser console, and `/market-fuzzer`. `make docker-smoke` builds the real image, waits for container health, and verifies the primary page, public hidden-data boundary, and advanced route from the host.

See [execution challenge contract](docs/EXECUTION_CHALLENGE.md), [architecture](docs/ARCHITECTURE.md), [five-minute judge path](docs/JUDGE_GUIDE.md), [testing](docs/TESTING.md), [performance evidence](docs/PERFORMANCE.md), [research references](docs/RESEARCH_REFERENCES.md), [Build Week provenance](docs/BUILD_WEEK_PROVENANCE.md), and [limitations](docs/LIMITATIONS.md).

## License and safety

MIT. No proprietary market data, arbitrary remote code execution, brokerage connection, or real-money trading is included. This is educational and research-testing infrastructure, not investment advice.
