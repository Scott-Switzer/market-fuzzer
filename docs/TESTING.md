# Testing

## Complete local verification

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m playwright install chromium
make verify
make docker-smoke
```

`make verify` is the required source verification gate. It runs:

```text
ruff format --check app tests
ruff check app tests
mypy app
pytest
determinism_check.py
provenance_check.py
demo_smoke.py
arena_smoke.py
browser_e2e.py
bash syntax and JavaScript syntax checks
git diff --check
```

The headless Chromium test starts an isolated server with a temporary SQLite database and no API key. It creates server-generated, separately signed student and instructor browser sessions and proves:

```text
student loads public challenge
→ hidden leaderboard is inaccessible
→ learner selects a policy and public practice renders
→ strict final policy is persisted
→ instructor locks submissions and evaluates hidden worlds
→ Aggressive POV public rank 1 differs from Guarded POV robustness rank 1
→ instructor releases without changing the evaluation
→ student receives the allowed released view and deterministic feedback
→ /market-fuzzer loads
→ browser console remains clean
```

API calls in this test use Playwright’s browser context, so they share the same HttpOnly cookie jar as the visible page. It is not an HTTP-only substitute for browser coverage.

## Test groups

- Security: server-generated/resumable identity, instructor-code enforcement, real-client rejection of the in-process test-auth bypass, loopback-versus-network cookie flags, fail-closed session-secret configuration, direct hidden-world/seed attacks, normal role-header rejection, public payload leak scans, pre-release replay/hash denial, released-field allow-listing, and permanent instructor-only raw world evidence.
- Metrics and lifecycle: fill-derived participation, order/ack/fill/cancel timestamp ordering, inventory identity, order hygiene, queue-ahead evidence, and deterministic replay.
- Submission: strict schema bounds, extra-field rejection, immutable final policy, shared engine adapter for built-ins and learner policies, practice/final limits.
- Ranking: exact public seed `42`, persisted hidden-world manifest plus protected `SEEDS`, and three fixed groups—`(41,)`, `(42,)`, and the production pair `(41, 42)`—with metric-derived rank, score decomposition, and deterministic matrix hash.
- Worlds and metamorphic relations: liquidity depth, latency ordering, crowded flow, event activation, transaction costs, same-input equality, and visibility-only release.
- Persistence: server-generated identity/session, challenge manifest, submission, phase, evaluation, feedback, challenge-design draft, release, and audit history survive `ArenaStore` reconstruction from the same SQLite file. Concurrent limit tests prove that quota count and insert are one immediate transaction, release tests prove phase, visibility, and audit update together, and cache tests prove that fully resolved `ARENA_DB_PATH` values select distinct bounded store instances.
- GPT: frozen structured-output corpus built from the same release-safe overall and educational-intent aggregate schema used in production, instructor-only qualitative design drafts, numeric-world rejection, stable public-trace evidence, allowed evidence IDs and values, hidden-release boundary, persisted-report recovery, score/rank immutability, refusal/incomplete handling, unsafe-claim rejection, and explicit no-key fallback.
- Browser: complete two-role lifecycle, visible ranking reversal and replay, stale-state clearing, responsive controls, clean console, and Market Fuzzer route.

Tests that use role headers must set `ARENA_TEST_AUTH=1`, use `X-Test-Role`, and run through Starlette's default in-process `testclient` peer. The same headers sent from any real or remote-like client scope are ignored even if the environment flag is present; production/demo requests never trust `X-Role`.

## Docker smoke

```bash
make docker-smoke
```

The target uses a unique Compose project and host port `18080` by default, builds the actual image, waits for its semantic Docker health check, and then verifies from the host:

- `/api/health` identifies Quant Challenge Arena;
- `/` contains the primary Arena;
- `/market-fuzzer` contains the protected advanced tool; and
- the public challenge JSON contains no protected world identifier.

It always removes the smoke container and volume. Override only the host port with `ARENA_DOCKER_PORT=18081 make docker-smoke`.

## Focused commands

```bash
.venv/bin/python -m pytest -q tests/test_execution_arena.py
.venv/bin/python -m pytest -q tests/test_execution_persistence.py
.venv/bin/python -m pytest -q tests/test_execution_auth_hardening.py
.venv/bin/python -m pytest -q tests/test_execution_challenge_designer.py
.venv/bin/python -m pytest -q tests/test_execution_feedback.py
.venv/bin/python scripts/browser_e2e.py
.venv/bin/python scripts/performance_probe.py
node --check app/static/arena.js
node --check app/static/app.js
```

The performance probe reports evidence only; it has no unstable timing threshold and is not part of `make verify`.

## GitHub Actions

`.github/workflows/ci.yml` runs the full source gate on Python 3.12, installs the pinned Playwright-compatible Chromium build, and runs Docker build/health smoke in a separate job. No live OpenAI key is required in CI; model calls are mocked and the no-key path is tested.

## Manual judge run

```bash
ARENA_DEMO_AUTH=1 \
ARENA_DEMO_INSTRUCTOR_CODE=change-this-local-demo-code \
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> and follow [JUDGE_GUIDE.md](JUDGE_GUIDE.md). Generated state defaults to `artifacts/arena.sqlite3`; use `ARENA_DB_PATH=/tmp/arena-demo.sqlite3` for an isolated run.
