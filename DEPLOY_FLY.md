# Deploying Fenrix to Fly.io

Everything is pre-configured (`Dockerfile` + `fly.toml` at repo root). You only need a
Fly account and the `flyctl` CLI. **No changes to app code are required** — the container
already binds `0.0.0.0:$PORT` and `fly.toml` sets `PORT=8000` / `internal_port = 8000`.

## ⚠️ Read this first

- **Credit card required.** Fly.io no longer has a permanent free tier. New accounts get a
  **free trial: 2 total VM hours or 7 days, whichever comes first** (trial machines
  auto-stop after 5 minutes of runtime). To keep an app running past that, you must add a
  credit card — after which billing is pure usage-based (a `shared-cpu-1x / 1GB` machine is
  roughly $5–7/mo if running 24/7, near $0 if it auto-stops when idle, which `fly.toml`
  enables).
- **RAM:** the app imports pandas + numpy + pyarrow + fastapi; a 256MB machine will OOM.
  `fly.toml` requests `shared-cpu-1x` with **1GB** memory.
- **Evidence artifacts are NOT in git.** `artifacts/` is gitignored, so a fresh deploy
  ships with an empty `/data/artifacts`. See "Persisting artifacts" below.
- **Docker-in-Docker limitation:** the strategy sandbox runtime shells out to `docker run`
  (`app/strategy_runtime.py`). Fly Machines do not provide a Docker daemon inside the VM,
  so sandboxed strategy execution endpoints will fail on Fly. The web UI, API, pitch deck,
  and evidence-browsing endpoints work fine.

## 1. Install flyctl and sign up

```sh
brew install flyctl            # macOS
fly auth signup                # opens browser; create the free account
# (or `fly auth login` if you already have one)
```

## 2. Launch (first deploy)

From the repo root (`/Users/scottthomasswitzer/Documents/OAI_Build_Week`):

```sh
cd ~/Documents/OAI_Build_Week
fly launch --copy-config --no-deploy   # reuses fly.toml; pick a unique app name + region
fly deploy                             # builds the Dockerfile remotely and deploys
fly open                               # opens https://<app>.fly.dev in your browser
```

Health check: the app must answer `GET /api/ready` with `status: ready` — `fly.toml`
already wires this up.

## 3. (Recommended) Persist the DB + artifacts with a volume

Without a volume, the sqlite DB and any generated artifacts vanish on every deploy/restart.

```sh
fly volumes create fenrix_data --size 1 --region <your-region>
```

Then uncomment the `[mounts]` block in `fly.toml` and run `fly deploy` again.

To upload the locally generated evidence package (gitignored, so not in the image):

```sh
fly ssh console -C "mkdir -p /data/artifacts"
fly ssh sftp shell     # then: put -r artifacts/submission /data/artifacts/submission
```

Alternative fix: remove `artifacts/submission/` from `.gitignore` (or add
`!artifacts/submission/**`) and add `COPY artifacts /data/artifacts` to the Dockerfile so
the evidence is pre-baked into the image at build time.

## 4. Secrets / env

No API keys are required to serve the app. If you later use OpenAI-backed features:

```sh
fly secrets set OPENAI_API_KEY=sk-...
```

## Useful commands

```sh
fly logs          # tail app logs
fly status        # machine state
fly machine list
fly deploy        # redeploy after changes
```

## Alternative: Render.com free tier (no credit card)

Render's Free web services need **no credit card**: 750 free instance-hours/month,
512MB RAM, deploys straight from a GitHub repo with the existing Dockerfile. Caveats:
spins down after 15 min idle (~1 min cold start), **ephemeral filesystem** (sqlite +
artifacts lost on every spin-down — same persistence problem, and free tier can't attach
disks), and 512MB may be tight for pandas/pyarrow (usually OK for serving, risky under
load). For a student demo where "no card" matters most, **Render is the easier choice**;
Fly is the better choice if you're willing to add a card and want a persistent volume.
