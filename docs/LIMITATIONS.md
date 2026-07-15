# Limitations
- Demo calibration is based on internally generated aggregates, not proprietary institutional order-book data.
- Queue-reactive intensities are regularized lookup behavior, not learned institutional policies or a Hawkes residual model.
- Several confidentiality checks remain `NOT_EVALUATED` without customer-side source-window access.
- Latency is discretized into clock steps rather than modeled at exchange-gateway nanosecond resolution.
- Circuit breakers use a simple reference-price threshold.
- POV observes recent synthetic volume; a production execution model would use more detailed volume forecasting and venue state.
- Only one synthetic venue and three equities are supported.
- No commercial or proprietary order-book data is included.
- A `FIT` component does not establish profitability, safety, production capacity, or fitness for live trading.
- This is research and testing infrastructure, not investment advice.
# Market Fuzzer scope boundary

Counterexamples are valid software regressions only within the configured synthetic environment. They are not forecasts, production capacity estimates, alpha evidence, live-trading approval, or regulatory-compliance evidence.
