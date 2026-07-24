# Deploy to Render (free web service, GitHub-connected)

Your Render account is already connected to GitHub, so this is mostly
point-and-click. Render's **free web service** is the right tier (Fly.io's
"free" tier is now a 7-day trial that requires a card and auto-stops).

## One-time setup (you do this in the Render dashboard)

1. Go to https://dashboard.render.com → **New +** → **Web Service**.
2. Connect the repo `Scott-Switzer/market-fuzzer`.
3. Render will detect `render.yaml` and pre-fill most fields. Confirm:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements-render.txt`
   - **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Plan:** Free
   - **Health Check Path:** `/api/ready`
4. Click **Create Web Service**. First build takes a few minutes; thereafter
   every `git push` to the deployed branch auto-redeploys.

## Alternative: no render.yaml (dashboard only)

If you skip `render.yaml`, set manually:
- Build Command: `pip install -r requirements-render.txt`
- Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

## What teammates get

- `https://<service>.onrender.com/submission` — the full interactive pipeline
  (compile → approve → backtest → stress → minimize → evidence download).
- `https://<service>.onrender.com/static/pitch-deck/index.html` — the deck.

## Free-tier caveats (important)

- **Spins down after 15 min idle**, then cold-starts on next visit (~30–60s).
  Fine for classmates poking around; not for production traffic.
- **Ephemeral disk:** evidence packages written under `artifacts/` disappear on
  redeploy. Live runs still work; the committed deck is served statically.
  For persistent evidence, attach a Render Disk and point
  `MARKET_FUZZER_ARTIFACT_ROOT` at it (see `render.yaml` envVars).
- **512 MB RAM** free tier. The dependency install is moderate (fastapi,
  numpy, pandas, pyarrow, pydantic) — fits comfortably.

## yfinance / live data on Render

The `yfinance` mode fetches real data at runtime. On Render this works but is
slower and rate-limited. For classroom demos, the `synthetic_fixture` (default)
and `auto` modes are instant and offline-safe.

## Verify after deploy

```
curl https://<service>.onrender.com/api/health      # -> {"status":"ok",...}
curl https://<service>.onrender.com/api/ready        # -> status ready
```

## Notes

- `fly.toml` + `DEPLOY_FLY.md` are also in the repo (valid config) but Fly's
  free tier is effectively dead — prefer Render.
- The repo's root `requirements.txt` is `-e .` (editable install, for local
  dev only) and will NOT work on Render's buildpack; `requirements-render.txt`
  is the deploy-correct list.
