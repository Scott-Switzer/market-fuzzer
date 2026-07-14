# Methodology

## Market mechanism

Each asset trades on a synthetic continuous double auction. Limit orders cross immediately when marketable; otherwise they rest at integer tick prices. Execution uses price priority followed by FIFO. Market orders walk resting levels. Owner-scoped cancellation, partial remainders, fees, inventory, cash, latency, and deterministic halts are logged.

## Synthetic world

Three fictional issuers have distinct fundamentals, macro betas, idiosyncratic volatility, liquidity profiles, and event sensitivity. A shared seeded factor and asset noise evolve fundamental values. Exchange prices are never assigned to fundamentals; they emerge from market-maker quotes and marketable agent orders.

## Counterfactual design

The normal, liquidity-withdrawal, earnings-shock, and crowded-unwind worlds preserve common seeds, assets, clock, and strategy. Mutation metadata states exactly what changes. The quick battery crosses four worlds, three participation rates, and two common seeds.

## Execution metrics

The engine reports filled quantity, fill rate, arrival price, market VWAP, average execution price, implementation shortfall, VWAP slippage, temporary and persistent impact, spread paid, adverse selection proxy, remaining inventory, maximum mark-to-market loss, market disruption, volume, fees, and runtime. These are simulation measurements, not forecasts.

## Realism diagnostics

Component checks include return excess kurtosis, raw and absolute-return autocorrelation, spread, depth, order-flow persistence, volume-volatility relationship, cross-asset correlation, and impact decay. Status values are limited to Pass, Partial, Fail, and Not evaluated. Directional literature targets draw from:

- Rama Cont, “Empirical properties of asset returns: stylized facts and statistical issues,” *Quantitative Finance* 1(2), 2001.
- Bouchaud, Farmer, and Lillo, “How markets slowly digest changes in supply and demand,” in *Handbook of Financial Markets*, 2009.
- Byrd, Hybinette, and Balch, ABIDES, arXiv:1904.12066.
- Frey et al., JAX-LOB, arXiv:2308.13289.
- Berti et al., TRADES, arXiv:2502.07071.
- Li et al., MarS, arXiv:2409.07486.

The prototype is not calibrated to a proprietary order-book dataset and makes no institutional-realism claim.

