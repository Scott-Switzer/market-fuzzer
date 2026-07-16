# Limitations and claim boundary

## Market model

- All assets, agents, events, orders, and datasets are fictional. No proprietary or live order-book data is included.
- The exchange is a bounded deterministic education simulator, not a certified venue emulator or production capacity model.
- Selected spread, depth, volatility, volume-clustering, signed-flow, and mechanical checks are **synthetic-market diagnostics**. They are not evidence of real-market calibration.
- The queue evidence reports displayed quantity ahead and trading at the level under the simulator’s price-time-priority model. It does not claim venue-specific hidden liquidity, microsecond gateway behavior, or a complete HftBacktest-style replay model.
- Latency is explicit by lifecycle stage but evaluated on the simulator clock; it is not nanosecond exchange-gateway fidelity.
- The current challenge uses one synthetic venue, three fictional equities, a fixed session horizon, and a limited background-agent ecology.
- Built-in benchmarks and hidden worlds were designed to teach bounded execution tradeoffs. A ranking reversal in these worlds does not establish a universal optimal policy.

## Assessment

- Public and hidden scores are challenge-relative educational rubrics, not expected returns, Sharpe forecasts, implementation-cost quotes, or certification scores.
- The default reversal is tested across deterministic seed groups, but this is not a statistical guarantee against all forms of leaderboard adaptation.
- Practice and final-submission limits reduce repeated probing; they do not provide the differential-privacy guarantees of a production reusable-holdout service.
- Explanations evaluate evidence use inside the declared challenge. They do not infer student intent, misconduct, authorship, or academic integrity.
- Only declarative policy configurations are accepted. Arbitrary strategy code, custom execution algorithms, remote workers, and untrusted container execution are intentionally out of scope.

## Authentication and deployment

- `ARENA_DEMO_AUTH=1` enables signed, HttpOnly demo sessions with server-generated, resumable local identities. Instructor issuance requires a server-configured local code, but this is still not OAuth/OIDC, institutional SSO, LMS identity, verified account recovery, multi-factor authentication, or public multi-tenant authorization. A resumed demo identity proves possession of its cookie, not a real person's identity.
- Demo cookies are `Secure` by default. The server omits `Secure` automatically only when both the peer and URL host are verified loopback (or in Starlette's in-process test scope). `ARENA_COOKIE_SECURE=0` is valid only with demo auth and cannot disable `Secure` for a non-loopback peer; other override values fail closed. Production must keep HTTPS-only secure cookies.
- Without `ARENA_SESSION_SECRET`, demo mode uses a process-random signing secret, so a restart invalidates otherwise persisted cookies. A configured secret must contain at least 32 bytes. A public deployment would additionally require managed secret rotation, CSRF protection, session rotation/revocation, rate limiting, user administration, backup/restore, and security review.
- `ARENA_TEST_AUTH=1` cannot authorize network requests: test-role headers are accepted only when the ASGI peer is exactly Starlette's in-process `testclient`. This hardens an accidental flag leak but is not a substitute for removing test settings from deployment configuration.
- SQLite immediate transactions enforce local practice/final quotas and atomic lifecycle/audit updates for the supported single-database demo. This does not claim distributed multi-instance scheduling, high availability, migrations across released schema versions, disaster recovery, or production observability.
- Docker and GitHub Actions prove repeatable build and smoke behavior, not a public hosted service or an SLA.

## GPT-5.6

- GPT-5.6 is an optional educational designer/analyst, not a world-construction or scoring engine. A design request can persist only a qualitative draft; it cannot create numeric worlds or change deterministic market events, orders, fills, metrics, scores, ranks, phase, or release state.
- Structured-output and grounding validation reduce unsupported statements; they do not guarantee that every pedagogical explanation is optimal.
- The frozen mocked eval corpus tests declared failure modes without making live API calls. It is regression coverage, not a comprehensive model-safety evaluation.
- With no API key, the UI shows a labeled deterministic template. It is not model-generated.

## Financial and regulatory boundary

Nothing in this repository proves profitability, alpha, future generalization, best execution, investment suitability, production safety, regulatory compliance, or fitness for live trading. It is not investment advice and has no brokerage or real-money connection.

Market Fuzzer counterexamples have the same boundary: they are reproducible software regressions within the configured synthetic harness, not forecasts or live-trading approval.
