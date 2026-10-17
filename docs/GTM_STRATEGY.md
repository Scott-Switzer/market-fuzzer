# Go-to-Market Strategy — Synthetic Market World

## Exact ICP
**Primary ICP:** Solo prop-shop quants and lean quant-tech leads at early-stage firms with 2–20 employees who already run Python backtests but lack a defensible execution-validation workflow.
**Secondary ICP:** University fintech/quant labs and bootcamp instructors who need reproducible, sealed assessment environments.

### ICP filters
- Uses Python, pandas/numpy, and local Jupyter or scripted research.
- Does not yet have a dedicated model-validation or execution-quality gate.
- Has experienced “backtest beats live” or leaderboard-overfitting pain.
- Can self-host or run Docker; no managed SaaS procurement required.
- Values reproducible artifacts over polished dashboards.

## Wedge Use Case
**Wedge:** “Can this strategy survive a hidden execution shock?”
First 30-day win: let a quant stress-test one existing strategy across 40 hidden synthetic worlds, get a plain-English failure report, and export a reproducibility-ready evidence package.
Use the execution-algorithm challenge surface (POV/TWAP/adaptive policies) as the fastest path to perceived value, because it maps directly to common prop-shop production workflows.

## Defensible Positioning
**“The only sealed, deterministic, self-hostable execution stress-test platform for quants who cannot accept black-box backtests.”**

### Differentiation against named competitors
- **QuantConnect / AlgoSeek / QuantRocket:** data-first backtest platforms. This product does not compete on data breadth; it competes on execution-quality evidence, hidden-family stress, and sealed reproducibility without uploading strategy code.
- **Numerai:** tournament/ML-first meta-model. This product is deterministic, code-transparent for your own workflow, and focused on execution robustness rather than ensemble meta-modeling.
- **Core moat:** single-engine determinism + family-holdout sealed evaluation + local evidence artifacts + bounded claims. These are hard to replicate at a startup price point because they require intentional governance, not just simulation fidelity.

## Positioning Statements
- **For the quant:** “Stop optimizing for a public backtest. Prove your policy survives hidden liquidity and latency shocks inside a sealed, reproducible environment.”
- **For the instructor:** “Assess execution thinking, not memorized code. Lock submissions, run hidden worlds, and release deterministic evidence.”
- **For the research team:** “Governed calibration, versioned worlds, and exportable validation reports without sending your strategy state to a third party.”

## Pricing Model
The shipped product is one tier: a local single-user install of this repository. Unlimited local runs. No credit card, subscription, or remote collaboration service.

Instructor-led arena sessions use that same install with `ARENA_DEMO_AUTH`. That flag is local demo authentication, not a separate commercial tier.

Calibration packs, Scenario Studio, Strategy Stress Lab, audit export, and HTTP adapter registration are part of the local install. Paid research, team, and private-pack licenses are not offered.

### Pricing rationale
- Self-hosting removes procurement friction for solo quants.
- The local model avoids multi-tenant security objections that slow regulated finance procurement.
- Local-only execution keeps cloud cost at zero.

## Distribution Model
### Immediate distribution (next 24 hours)
- **GitHub + PyPI:** publish as `synthetic-market-world`. README leads with the “backtest vs hidden execution” reveal.
- **Docker Hub:** single `docker compose up --build` path for enterprise + demo surfaces.
- **Hackathon/judge package:** HACKATHON_RUNBOOK.md and one-command `make judge-demo` for repeatable evaluations.

### Growth distribution
- **PLG motion:** the local repository is the product. Governed calibration packs and experiment jobs run on that install.
- **Community-led:** open-source core, closed enterprise extensions. Encourages quant-education communities and fintech bootcamps to adopt the instructor challenger surface as a teaching benchmark.
- **Direct outreach:** cold outreach to quant-tech leads at firms running Python execution research; lead with deterministic evidence and sealed evaluation, not simulation prettiness.
- **Partnership wedge:** integration-ready calibration boundary and external HTTP adapter make it easy for data vendors to certify their OHLCV/MBP/MBO feeds for use inside the platform once their license allows it.

## Launch Checklist
1. Publish README with ICP, wedge, and 3-step demo clearly on the front page.
2. Keep the README pricing section limited to the local self-hosted install.
3. Create `docs/START_HERE.md` for solo quants: install → load demo strategy → run hidden evaluation → export evidence.
4. Enforce open-core boundary in docs: deterministic engine is MIT, enterprise API extensions may move to a commercial add-on package.
5. Prepare DM/email template for quant outreach: observable pain → sealed test → 30-minute local demo.

## Risks and Mitigations
- **“Too educational” objection:** counter with enterprise calibrations, private packs, and governance workflows.
- **“No live data / no real broker” objection:** make calibration data and external adapter the explicit upgrade, not a missing core feature.
- **“I can build this internally” objection:** race to governed reports, family-holdout sealed evaluators, and external-adapter ecosystem; these are operational work, not simulation code.
- **Support burden:** local-first implementation and README-first support keep cloud cost near zero.
