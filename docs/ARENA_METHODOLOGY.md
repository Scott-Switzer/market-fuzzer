# Secondary research/positions challenge methodology

## Purpose

This experimental secondary challenge tests whether a student's position file continues to support its stated mechanism after controlled hidden changes to a fictional data-generating process. The primary Quant Challenge Arena now uses the exchange-backed policy contract documented in `EXECUTION_CHALLENGE.md`.

## Public and hidden regimes

The public panel contains eight dates and three fictional assets under a momentum regime. The hidden panel contains another eight dates split across four two-day regimes:

1. Structural break: the public relationship changes sign.
2. Lookahead trap: a one-period delay changes the return mapping.
3. Liquidity shock: transaction costs increase.
4. False predictive feature: the tempting public feature changes behavior.

The challenge seed, generator version, schema version, and specification hash are recorded. The public dataset omits regime labels and all hidden dates. The instructor dataset contains latent labels and is generated only in instructor-scoped code paths.

## Submission contract

Students submit exactly `date,asset,position`. Every public date and asset must appear exactly once. Positions are finite, bounded to ±1, and must satisfy gross exposure ≤ 1.5 and absolute net exposure ≤ 1.0 per date. Hidden dates, unknown assets, duplicate keys, malformed dates, and non-finite values are rejected.

## Deterministic metrics

The evaluator calculates public and hidden return series from the submitted positions and generated panel. It reports annualized return and volatility, Sharpe, drawdown, turnover, estimated costs, exposure, concentration, hidden regime decomposition, cost sensitivity, one-day delay sensitivity, liquidity-shock sensitivity, and feature-collapse sensitivity.

The operational sensitivities are average per-period basis-point changes. This avoids using Sharpe differences when a deterministic fixture has near-zero variance. A robustness score is a weighted summary for ranking within this challenge only; it is not a universal model-quality score.

## Ranking reversal fixture

Example A (`backtest_winner`) concentrates in the public winner and receives the higher public score. Its hidden Sharpe collapses after the structural break. Example B (`robust_generalizer`) has a lower public score but retains positive hidden performance and wins the robustness-adjusted ranking. The reversal is a deterministic teaching fixture, not a claim about real markets.

## GPT boundary

GPT-5.6 may produce structured challenge prose, failure hypotheses, and feedback. Every response is validated against a Pydantic schema. GPT never receives or returns hidden data through student endpoints, and never calculates scores, ranks, or verdicts. No-key mode uses deterministic generation and feedback so the full workflow is reproducible offline.
