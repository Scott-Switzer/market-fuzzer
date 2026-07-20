# Strategy runtime security boundary

`http_json_v1` is a legacy development bridge, not an isolated strategy
runtime. It is disabled by default and can be enabled only for explicitly
scoped local compatibility testing with `ARENA_ALLOW_LEGACY_HTTP_ADAPTER=1`.

Production evaluation must fail closed for HTTP callbacks. M8 replaces this
bridge with a digest-pinned isolated session that has no default egress,
bounded messages and resources, durable response recording before order
admission, idempotency keys, and deterministic crash/timeout outcomes. Until
that runner is implemented and tested, no customer-supplied executable strategy
is production-eligible.
