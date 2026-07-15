# Fuzzing methodology

Quick search evaluates deterministic bounded scenarios with common seeds. It searches liquidity, volatility, latency, spread, replenishment, and forced flow, requires two failures from three seeds, then coordinate-reduces the failing case. Severity policy `severity-1.0` displays every component and is not an industry standard.
