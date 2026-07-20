# Release candidate audit

## Supported claim

This is a single-machine release candidate for reproducible sealed evaluation of digest-pinned container strategies across three declared synthetic generator families. Primary worlds are committed before artifact freeze and selected independently of strategy behavior. Adaptive diagnostics remain separate.

## Security boundary

- Customer code runs in a no-network, bounded container; the application process does not import it.
- The optional worker mounts the Docker socket and is therefore a trusted single-host operator component, not a multi-tenant security boundary.
- Enterprise authentication is one API key when configured. TLS, tenant isolation, public-key signing, HA, and cloud key custody are not provided.
- Evidence HMACs authenticate artifacts to an operator-held secret; they are not third-party or public-key attestations.

## Operational gates

- SQLite uses WAL, full synchronous writes, foreign keys, busy timeout, and integrity-checked readiness.
- Jobs persist before execution, use renewable time-bounded claims, and recover expired leases after restart.
- Backup and atomic restore both verify checksums and SQLite integrity.
- Artifact reads recompute stored content and manifest digests and fail closed on mismatch.
- Structured request logs contain identifiers and timing but no request bodies or secrets.
- The bounded load smoke is a regression gate, not a capacity certification.

## Unsupported claims

No live profitability, unbiasedness, impossible memorization, universal exchange realism, options/fixed-income/OTC fidelity, multi-tenant isolation, HA, or production readiness is claimed.
