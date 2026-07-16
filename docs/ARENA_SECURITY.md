# Arena security and visibility boundary

The primary Execution Challenge uses signed demo sessions, persisted identities, explicit phases, and allow-listed release views. A normal client header is not an authorization boundary.

## Demo identity

- Demo sessions exist only when `ARENA_DEMO_AUTH=1`.
- Instructor issuance additionally requires the server-only `ARENA_DEMO_INSTRUCTOR_CODE` using a constant-time comparison.
- The request supplies only the desired role and, for an instructor, the code. It cannot choose a user ID; the server generates that identity.
- The server returns signed, HttpOnly, SameSite=Lax generic and role-specific cookies. Their hash, generated user ID, role, issue time, and expiry are recorded in SQLite. Cookies are `Secure` by default. Only a request whose peer and URL host are both loopback (plus Starlette's exact in-process test scope) gets the HTTP-local exception automatically.
- `ARENA_COOKIE_SECURE=1` forces `Secure`. `ARENA_COOKIE_SECURE=0` is valid only while `ARENA_DEMO_AUTH=1` and is effective only after the same loopback/test-scope check; it cannot disable `Secure` for a network peer. Other values fail closed.
- Reissuing a role while its role-specific cookie is valid resumes the same persisted identity. This preserves practice limits, drafts, final submission ownership, and released-report recovery across reload. If `ARENA_SESSION_SECRET` is omitted, demo mode generates a process-random secret and deliberately invalidates cookies after restart. A stable restart-resumable deployment must supply a secret of at least 32 bytes.
- The instructor code is neither stored in a cookie nor returned to the browser.
- `X-Role` has no authority. `X-Test-Role` and `X-Test-User` work only when `ARENA_TEST_AUTH=1` **and** the ASGI request peer is exactly Starlette's in-process `testclient`; the bypass is unreachable to real network clients even if the environment flag is accidentally set.

This is local prototype authentication, not production identity. See `docs/LIMITATIONS.md` for the deployment hardening still required.

## Execution data visibility

Before release, student endpoints may return:

- the public challenge brief and protected-test count;
- strict policy definitions and stored limits;
- the derived public world result, public replay, and public score;
- the learner’s own immutable policy/submission record; and
- a public leaderboard without hidden metrics.

They do not return hidden identifiers, interventions, parameters, hashes, world results, replays, or feedback. The public practice payload has no world-variant or seed override; the server reads the public variant from challenge state and uses seed `42`.

Instructor sessions may lock submissions, initiate the server-selected hidden matrix, inspect raw stored evidence/audit history, release the allow-listed aggregate view, and archive the challenge. Hidden evaluation requires the correct phase, reads its protected world IDs from the persisted challenge manifest, uses internal `SEEDS = (41, 42)`, and is stored before release. Client payloads cannot replace that manifest or seed tuple.

After release, students receive only declared aggregate rank/score fields and coarse educational-intent aggregates. These intent aggregates contain measured summaries such as latency, displayed depth, forced flow, completion, participation, and shortfall; they omit seed rows, internal world identifiers, hashes, orders, fills, and protected replay. The analyst is grounded with those release-safe fields and stable hashed identifiers from the student's own public replay. `world_results`, raw world hashes, detailed hidden replay, and audit evidence remain instructor-only even after release and are not copied into student feedback. Release updates visibility, time, and its audit entry atomically; it does not mutate scores or the matrix hash. Once generated, the feedback report is stored and recovered on later requests instead of invoking the analyst again.

## Transaction boundary

State-changing lifecycle operations and their audit rows commit in one SQLite transaction. Practice and final-submission quota checks use `BEGIN IMMEDIATE` around both count and insert, preventing two concurrent requests from both passing the same stale count in the supported single-database deployment. Release likewise commits evaluation visibility, challenge phase, phase history, and audit evidence as one state change. API store objects use a bounded process cache keyed by the fully resolved `ARENA_DB_PATH`; each cached store seeds the default challenge once, and changing the path cannot silently reuse the previous database.

## Challenge-design boundary

Only an instructor session can request a GPT-authored challenge-design draft. The strict draft is qualitative, limited to approved educational intents and policy controls, and persisted with an audit event. Validation rejects numeric market parameters, seeds, prices, quantities, latency values, metrics, scores, ranks, and model-authored outcomes. A draft cannot create or mutate a numeric hidden world.

## Submission boundary

Execution submissions are strict Pydantic policy objects. Extra fields, invalid enums/bounds, non-finite values, and incompatible controls are rejected. The API does not execute uploaded Python, arbitrary containers, shell commands, or remote URLs.

The secondary research/positions challenge retains its bounded CSV contract under `/api/arena/challenges/...`; it is experimental and does not define the primary Execution Challenge security model.
