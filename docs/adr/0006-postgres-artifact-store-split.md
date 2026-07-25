# ADR-0006: PostgreSQL + ArtifactStore split

- **Status:** Accepted (Phase 1)
- **Date:** 2026 reset

## Context
At baseline, run outputs were written to raw `artifacts/submission/<hash>/`
directories and referenced by filesystem-path strings; there was no durable
metadata store (campaign persistence was local/ad-hoc), and long jobs ran
in-process (lost on API restart). Reset brief section 11 and integrity gates 22/23
require durable authoritative metadata and artifacts addressed through an
interface that never leaks local filesystem paths into public responses.

## Decision
Split durable state into two stores:

1. **PostgreSQL** is authoritative for projects, strategies, strategy versions,
   approvals, runs, run stages, jobs, data-source registrations, failure
   records, artifact **indexes**, and evidence verification state. Modeled with
   SQLAlchemy 2 (`app/persistence/models.py`), migrated with Alembic
   (`app/persistence/migrations/`). SQLite is used for local dev and tests only;
   the models are written to be portable.

2. **`ArtifactStore`** (`app/evidence/artifact_store.py`) holds the actual
   artifact bytes, addressed by opaque keys. Implementations: `Filesystem`
   (local dev, atomic writes, traversal-guarded, confined to a root),
   `InMemory` (tests), and a specified S3-compatible impl for production. The DB
   stores only an index row (`store`, `key`, `sha256`, `size`, `content_type`) —
   never a blob, never an absolute path. `ArtifactRef.public_dict()` is proven by
   test to contain no filesystem path, and `public_url()` returns an
   `artifact://` handle, not a disk path.

## Consequences
- Integrity gate 22 (no local FS paths in public responses) is structurally
  enforceable; the public API returns `ArtifactRef.public_dict()`.
- Artifacts can move to S3 in production with no change to callers.
- Reproducibility does not depend on committed artifacts (which remain
  gitignored); CI regenerates deterministically.

## Migration note
The existing `submission/evidence.py` writer keeps working during Phase 1; Phase
7 routes it through the `ArtifactStore` and indexes outputs in the DB, then the
raw-path writes are removed.
