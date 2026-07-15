# Quant Challenge Arena — Devpost draft

## Tagline

The public practice winner is not always the robust execution winner.

## Elevator pitch

Configure an execution policy, practice in a visible synthetic exchange, then learn why hidden liquidity, latency, crowding, and event regimes reverse the leaderboard.

## Category

Education.

## Inspiration

Visible leaderboards are useful but easy to optimize narrowly. In quantitative courses and interviews, a participant can appear strong because one public backtest rewards speed or concentration. The more valuable question is whether the participant understands how the strategy behaves when liquidity disappears, messages arrive later, order flow crowds, or a scheduled event changes the path.

## What it does

Quant Challenge Arena gives a learner a fictional 6,000-share NOVA parent order and a strict declarative execution-policy editor. A server-generated, resumable demo identity owns the learner's limited public practice at seed `42`, synchronized replay, drafts, and one final policy. An instructor authenticated with the server-only demo code locks submissions and evaluates the persisted protected-world manifest with the internal deterministic seed tuple.

The public score is intentionally easy to understand: completion, nonnegative implementation shortfall, terminal inventory, and excess above a visible 25% participation cap. The released robustness view adds measured mean/worst execution, impact, participation discipline, inventory path, and stability only when those components are backed by the exchange. The default production matrix produces the teaching reveal: Aggressive POV leads public practice while Guarded Adaptive POV leads hidden robustness. That movement is derived from engine metrics; no rank is keyed to a policy name.

Before release, students receive only a protected-test count. They cannot choose a hidden world, enumerate its parameters, retrieve its hash or replay, or gain instructor authority with a client header. After release, they receive an allow-listed overall and educational-intent aggregate report while seed rows, internal world identifiers, hashes, and raw world evidence remain instructor-only.

## How we built it

- Python, FastAPI, Pydantic, SQLite, NumPy, HTML/CSS/JavaScript, and a deterministic price-time-priority synthetic exchange.
- A versioned policy schema for TWAP, POV, and adaptive POV controls; no arbitrary participant code execution.
- Explicit observation, decision, submission, exchange-arrival, acknowledgment, fill, cancel-request, and cancel-effective timestamps.
- Fill-derived participation, inventory invariants, order hygiene, queue-ahead evidence, deterministic replay, and immutable evaluation hashes.
- Server-generated, resumable signed HttpOnly demo sessions, an instructor-code gate, and phase-aware challenge routes.
- Transactional SQLite quota enforcement and atomic lifecycle/audit updates.
- GPT-5.6 structured evidence analysis with local evidence-ID, metric, numeric, release, financial-claim, and deterministic-outcome validation.
- Pytest deterministic and metamorphic cases, Ruff, Mypy, Playwright Chromium E2E, Docker health smoke, and GitHub Actions.

## GPT-5.6 and Codex

GPT-5.6 adds legitimate educational value in two bounded roles: it can propose and persist a qualitative lesson-design draft constrained to approved intervention intents, and it can explain release-safe overall/intent aggregates plus stable IDs from the learner's public trace. It never creates or mutates numeric worlds, and never generates prices, orders, fills, scores, ranks, or release state. Raw protected world evidence remains instructor-only. Responses use a strict schema; refusals and incomplete output are explicit. Validated feedback reports persist for recovery after reload or restart. The no-key path displays a labeled deterministic explanation rather than pretending a model ran.

Codex was used across the build to inspect and preserve the existing Market Fuzzer milestone, research architecture references, implement the exchange-backed Arena and persistence boundary, repair hidden-world security and metrics, build the GPT grounding/eval suite, create the replay and lifecycle UI, and run source, browser, and container verification.

**Before submission:** add the final Codex `/feedback` session ID here.

## Technical architecture

```text
Primary        Execution Challenge Arena
Secondary      Research/positions challenge (experimental)
Advanced       Market Fuzzer at /market-fuzzer
Infrastructure Synthetic world, agents, and exchange
```

SQLite stores users, sessions, challenge manifests and phase history, practice runs, policy submissions, hidden evaluations, per-world results, leaderboard snapshots, qualitative design drafts, feedback reports, and audit events. Quota checks and inserts are one immediate transaction; release commits visibility, phase, and audit state together without changing the matrix. The Docker image runs as non-root and persists state in a named volume.

## Challenges we ran into

The first execution UI was a benchmark viewer rather than a real student challenge. Public routes also accepted world selection too close to the simulator boundary, participation was initially inferred from market volume rather than strategy fills, and the replay hid most of the order lifecycle. We repaired those by making the phase and role boundaries explicit, storing strict policies and immutable matrices, instrumenting the exchange lifecycle, and requiring UI claims to be backed by evidence rows.

The other hard boundary was GPT. A fluent explanation is not evidence. We built a frozen eval corpus and reject model output that cites an unknown evidence ID, invents a metric or number, discloses hidden information early, makes investment/production claims, or contradicts deterministic rank.

## Accomplishments

- A complete no-key instructor/student workflow that survives application restart.
- A real public-versus-hidden ranking reversal across deterministic exchange runs.
- Hidden-world protection before release and permanently protected raw evidence.
- A synchronized replay with market state, orders, fills, inventory, participation, latency, and events.
- A preserved advanced Market Fuzzer route and protected milestone tag.
- One-command Docker launch plus browser and container CI coverage.

## What we learned

A credible simulation product should state what each result is fit to support. Mechanical validity, deterministic accounting, and selected synthetic diagnostics are useful; they do not establish real-market calibration. The same discipline applies to AI: GPT is most valuable when it makes verified evidence teachable, not when it substitutes prose for an exchange or scoring system.

## What’s next

Institutional OIDC/LMS integration, CSRF and public multi-tenant hardening, richer challenge authoring, venue-specific calibration using properly licensed data, and a hosted classroom pilot. None is claimed in the current prototype.

## Testing

Judges can follow `docs/JUDGE_GUIDE.md`, run `make verify`, and run `make docker-smoke`. No API key or market-data account is required. `/market-fuzzer` remains operational as the advanced failure-minimization lab.

## Limitations

This is a deterministic fictional assessment environment. It does not prove alpha, future profitability, best execution, production safety, real-market calibration, participant intent, or regulatory compliance. The signed demo session is not institutional authentication. The project does not execute participant code or connect to a brokerage.

## Submission fields still required

- Public repository: <https://github.com/Scott-Switzer/market-fuzzer>
- Public demo URL: **TBD**
- Demo video under three minutes: **TBD**
- Screenshots: **TBD**
- Final commit SHA and GitHub Actions URL: **TBD**
- Codex `/feedback` session ID: **TBD**

## License

MIT. No substantial code from the research references was copied or vendored.
