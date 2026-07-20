# Strategy runtime security boundary

`http_json_v1` is a legacy development bridge, not an isolated strategy
runtime. It is disabled by default and can be enabled only for explicitly
scoped local compatibility testing with `ARENA_ALLOW_LEGACY_HTTP_ADAPTER=1`.

Production evaluation must fail closed for HTTP callbacks. M8 replaces this
bridge with a digest-pinned isolated session that has no default egress,
bounded messages and resources, durable response recording before order
admission, idempotency keys, and deterministic timeout outcomes. The response
journal is SQLite-backed and records before the matcher receives an action.
Before each decision the journal is checked by idempotency key; a persisted
response is replayed without re-running customer code. A timeout or runner
crash is persisted as a deterministic no-action response, so a retry cannot
issue a different order. This runtime boundary does not make the overall
product production-ready.
