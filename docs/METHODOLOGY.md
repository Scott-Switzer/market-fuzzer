# Methodology

## Market mechanism

Each asset trades on a synthetic continuous double auction. Limit orders cross immediately when marketable; otherwise they rest at integer tick prices. Execution uses price priority followed by FIFO. Market orders walk resting levels. Owner-scoped cancellation, partial remainders, fees, inventory, cash, latency, and deterministic halts are logged.

## Synthetic world

Three fictional issuers have distinct fundamentals, macro betas, idiosyncratic volatility, liquidity profiles, and event sensitivity. A shared seeded factor and asset noise evolve fundamental values. Exchange prices are never assigned to fundamentals; they emerge from market-maker quotes and marketable agent orders.

## Calibration and counterfactual design

Canonical source rows are used transiently and split chronologically 60/20/20 into train, validation, and held-out test windows. Only aggregate targets, uncertainty, provenance, and hashes are retained. Quick mode preserves three accepted bootstrap parameter sets plus rejected-set evidence.

The queue-reactive provider conditions six event types on spread, depth, imbalance, recent flow, volatility, last event, and intervention state, with sparse-state backoff. The quick campaign combines 50% displayed-depth reduction and a forced seller while crossing 2%, 5%, 10%, and 20% participation over eight paired seeds and three accepted calibrations.

## Execution metrics

The engine reports filled quantity, fill rate, arrival price, market VWAP, average execution price, implementation shortfall, VWAP slippage, temporary and persistent impact, spread paid, adverse selection proxy, remaining inventory, maximum mark-to-market loss, market disruption, volume, fees, and runtime. These are simulation measurements, not forecasts.

## Fit-for-use validation

The five vectors are mechanical validity, calibration stability, statistical fidelity, interventional fidelity, and downstream utility. Verdicts are `FIT`, `LIMITED`, `FAIL`, and `NOT_EVALUATED`. The execution-cost claim requires Spearman rho ≥ 0.70, positive paired changes ≥ 0.70, a positive bootstrap-slope lower bound, and calibration-set agreement ≥ 0.80.

Structural properties are labeled as imposed assumptions; impact and costs in calibrated worlds are labeled as observed emergent outputs. The only Build Week use-case verdict is `execution_stress_testing`; production capacity estimates are always blocked.

Directional literature targets draw from:

- Rama Cont, “Empirical properties of asset returns: stylized facts and statistical issues,” *Quantitative Finance* 1(2), 2001.
- Bouchaud, Farmer, and Lillo, “How markets slowly digest changes in supply and demand,” in *Handbook of Financial Markets*, 2009.
- Byrd, Hybinette, and Balch, ABIDES, arXiv:1904.12066.
- Frey et al., JAX-LOB, arXiv:2308.13289.
- Berti et al., TRADES, arXiv:2502.07071.
- Li et al., MarS, arXiv:2409.07486.

The release report separately checks exact-row leakage, nearest source-window similarity, source-trajectory correlation, license eligibility, and public/private boundaries. Membership inference is `NOT_APPLICABLE` because no provider is trained on source records.
