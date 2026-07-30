# ADR-0001: Modular monolith, not microservices

- **Status:** Accepted (Phase 1)
- **Date:** 2026 reset, branch `reset/fenrix-product-reset-v1`

## Context
Fenrix must be operable by a small team and independent quants on a single
developer laptop, while remaining credible to allocator/research teams. The
reset brief (section 3.3) explicitly prohibits Kubernetes, microservices, Kafka, Spark,
Ray, and a feature store during this reset.

## Decision
Build a **modular monolith**: one FastAPI application process plus **separately
runnable worker processes** (Celery). Modules are separated by clear package
boundaries (`app/domain`, `app/persistence`, `app/evidence`, `app/compiler`,
`app/historical`, `app/synthetic`, `app/exchange`, `app/jobs`) with dependencies
pointing inward toward `app/domain`. No network boundary between modules.

## Consequences
- Single deployable + worker; simple local `docker-compose` (api, worker,
  rabbitmq, postgres).
- Refactors across module boundaries are compile-time checked, not runtime
  contract drift.
- If a component later needs independent scaling, its package boundary is
  already the seam to extract. We do not pay distributed-systems cost now.
