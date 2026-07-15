# Market Fuzzer demo script — 2:55 target

**0:00–0:20 — Problem.** “A strategy can pass a normal backtest and still violate its execution requirements when liquidity and observations change. Market Fuzzer searches for the smallest reproducible synthetic condition that breaks it.”

**0:20–0:40 — Strategy and safety.** Start the fragile POV tutorial. Show the parent order and requirements: completion, shortfall, participation, halt behavior, and remaining inventory.

**0:40–0:55 — Baseline.** Run the normal market. Show `Baseline PASS` and the measured metrics.

**0:55–1:25 — Break.** Click **Break My Strategy**. The deterministic bounded search targets participation, not an arbitrary failed metric.

**1:25–1:50 — Counterexample.** Show the minimized scenario, targeted threshold/observed value, seed reproduction, and the separately verified passing neighbor.

**1:50–2:10 — Replay.** Open the synchronized replay and point to stale observed volume, forced flow, depth, fills, and the first violation step.

**2:10–2:30 — Fix and retest.** Click **Retest with corrected POV**. Show the exact scenario hash, identical seeds and parent order, fragile `FAIL`, and corrected `PASS`.

**2:30–2:45 — Regression.** Export YAML/JSON and run the regression suite. Show actual fixture execution and status, including any invalid legacy fixtures rather than hiding them.

**2:45–2:55 — OpenAI role and close.** Click **Explain failure with GPT-5.6** if an API key is configured; otherwise show the explicit deterministic fallback. “GPT-5.6 can propose grounded hypotheses and explain measured evidence; deterministic code decides results. Codex built and verified the workflow. This is a software regression inside a bounded synthetic harness, not a live-trading claim.”
