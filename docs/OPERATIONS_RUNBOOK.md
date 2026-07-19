# Synthetic Market World operator runbook

This runbook describes the supported single-tenant research-appliance
deployment. It is deployable and restartable, but it is not a claim of hosted
multi-tenant production infrastructure, an OMS, or live execution safety.

## Start the appliance

```bash
export ARENA_ENTERPRISE_API_KEY="replace-with-a-long-random-secret"
export ARENA_ADAPTER_ALLOWED_HOSTS="127.0.0.1,localhost"
docker compose up --build -d
curl -fsS http://127.0.0.1:8000/api/health
```

The API key protects every `/api/enterprise/*` route when configured. Supply it
as `X-API-Key` or `Authorization: Bearer ...`. The health route stays public so
Docker and an operator can check liveness. Rotate the key by recreating the
container with a new environment value.

The container runs as a non-root user and stores SQLite plus artifacts in the
named `arena-data` volume. The supported deployment is one application process
and one SQLite database. Do not mount the database into multiple writers.

## Backups and recovery

Stop writes before copying the volume or use SQLite's online backup procedure:

```bash
docker compose exec quant-challenge-arena \
  python -c "import sqlite3; src=sqlite3.connect('/data/arena.sqlite3'); dst=sqlite3.connect('/data/arena-backup.sqlite3'); src.backup(dst); dst.close(); src.close()"
docker compose cp quant-challenge-arena:/data/arena-backup.sqlite3 ./arena-backup.sqlite3
```

Preserve `/data/artifacts` with the database. Experiment artifacts include
content hashes and manifests that reference world hashes, scenario packs, seeds,
creator, and schema versions. A restored database and artifact directory should
be treated as one evidence set.

## Local market data and licensing

Use `scripts/build_local_calibration_pack.py` to compile local OHLCV Parquet
into aggregate-only evidence. The source file checksum is retained; source rows
are not. Do not bake proprietary or unlicensed raw data into the image. The
operator must record the usage basis, instrument, venue, session, and retrieval
date in the calibration pack.

OHLCV-derived spread/depth/signed-flow fields are proxies. Queue position,
order-arrival, cancel, and market-impact claims require licensed order-event
data and a future MBP/MBO adapter.

## External adapter operations

Register an adapter with `adapter_id=http_json_v1`, a semantic version, and an
endpoint URL. The endpoint host must appear in `ARENA_ADAPTER_ALLOWED_HOSTS`.
The adapter receives a strict `strategy_observation_v1` JSON object and must
return a strict `execution_action_v1` object. It is not allowed to mutate the
world or submit directly to an OMS. The API applies the action inside the
synthetic exchange and records the adapter contract hash and runtime boundary.

If a bearer token is needed, set `auth_env_var` to the name of an environment
variable and provide that variable to the application container. Secrets are
never placed in strategy records or artifact manifests. Use a short timeout and
`error_policy=fail_cell` for research runs so an adapter failure cannot silently
become a passing result.

## Release checklist

1. Import an authorized calibration source and inspect checksum, windows, and
   held-out stability.
2. Compile the scenario pack and run its regression suite.
3. Register the plain-English proposal or HTTP adapter and inspect its contract.
4. Run at least two deterministic seeds and review the artifact manifest.
5. Export the validation JSON and retain the artifact plus manifest hash.
6. Record the exact Git commit and Docker image digest used for the decision.

The final report should state the evidence tier and limitations. A governed
`FIT` or `LIMITED` result is a claim about the declared synthetic configuration,
not proof of profitability, best execution, or live-market generalization.
