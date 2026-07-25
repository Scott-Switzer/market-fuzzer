# Deploy to Render (free web service, GitHub-connected)

Your Render account is already connected to GitHub, so this is mostly
point-and-click. Render's **free web service** is the right tier (Fly.io's
"free" tier is now a 7-day trial that requires a card and auto-stops).

## One-time setup (you do this in the Render dashboard)

`render.yaml` in this repo is a **Blueprint** (IaC). The correct deploy path is:

1. Go to https://dashboard.render.com → **New +** → **Blueprint**.
2. Connect the repo `Scott-Switzer/market-fuzzer`.
3. Render detects `render.yaml`. It will deploy the **branch pinned in the
   file** (`branch: fix/fenrix-submission-final-hardening`) — NOT your repo's
   default branch. Confirm the branch is the one you want tested.
4. Plan = Free. Health check = `/api/ready`.
5. Click **Apply** / **Deploy**. First build takes a few minutes; thereafter
   every push to that branch auto-redeploys once CI checks pass
   (`autoDeployTrigger: checksPass`).
6. For the FINAL deploy, merge this stack into the branch Render follows
   (or change the `branch:` field) before deploying.

> If you prefer **New + → Web Service** instead of Blueprint: Render will NOT
> read `render.yaml` automatically. Enter manually: Runtime=Python 3,
> Build Command=`pip install -r requirements-render.txt`,
> Start Command=`uvicorn app.main:app --host 0.0.0.0 --port $PORT`,
> Branch = `fix/fenrix-submission-final-hardening`.

## What teammates get

- `https://<service>.onrender.com/submission` — the full interactive pipeline
  (compile → approve → backtest → stress → minimize → evidence download).
- `https://<service>.onrender.com/static/pitch-deck/index.html` — the deck.

## Free-tier caveats (important — corrected)

- **Spins down after 15 min idle**, then cold-starts on next visit (~30–60s).
  Fine for classmates poking around; not for production traffic.
- **Free web services CANNOT use persistent disks.** The filesystem is wiped on
  every redeploy, restart, or idle spin-down. The committed deck
  (`app/static/pitch-deck/`) survives because it ships in the repo, but
  **generated evidence, the yfinance cache, and SQLite state do NOT persist**.
  Live runs still work; they just regenerate each time.
- **Persistence requires either** Postgres/object storage for state, or a paid
  instance with an attached disk. Do NOT attach a disk to the free plan — it is
  unsupported.
- The `MARKET_FUZZER_ARTIFACT_ROOT` env var is set in `render.yaml` for when the
  app's artifact paths are wired to honor it, but **today the app still writes
  relative `artifacts/` paths**, so that variable does not yet create
  persistence. Treat evidence as temporary until that wiring lands.

## yfinance / live data on Render

`requirements-render.txt` includes `yfinance`, so the `yfinance` / `auto` modes
fetch real data at runtime (slower, rate-limited). For classroom demos the
`synthetic_fixture` (default) mode is instant and offline-safe.

## Verify after deploy

```
curl https://<service>.onrender.com/api/health      # -> {"status":"ok",...}
curl https://<service>.onrender.com/api/ready        # -> status ready
```

## Clean deploy smoke test (local, only requirements-render.txt)

This proves the deployment dependency set installs and the app boots WITHOUT
the dev-only `-e .` editable install:

```
python -m venv /tmp/render-venv
/tmp/render-venv/bin/pip install -r requirements-render.txt
/tmp/render-venv/bin/python -c "import app.main; print('import ok')"
/tmp/render-venv/bin/python -m uvicorn app.main:app --port 8011 &
curl -s localhost:8011/api/ready
```

## Notes

- `fly.toml` + `DEPLOY_FLY.md` are also in the repo (valid config) but Fly's
  free tier is effectively dead — prefer Render.
- The repo's root `requirements.txt` is `-e .` (editable install, for local
  dev only) and will NOT work on Render's buildpack; `requirements-render.txt`
  is the deploy-correct list.
