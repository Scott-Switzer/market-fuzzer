# Fuzzing methodology

Quick search evaluates deterministic bounded scenarios with common seeds. It searches liquidity, volatility, latency, spread, replenishment, and forced flow, requires two failures from three seeds, then coordinate-reduces the failing case. The current severity policy is `severity-2.0`; it displays every component and is not an industry standard.

GPT-5.6 may rank hypotheses and explain a verified failure, but it cannot submit orders, calculate a property, or select a verdict. The no-key deterministic fallback is the reference path.
