# Limitations
- Agent rules are transparent heuristics, not calibrated institutional policies or learned responsive order flow.
- Quick-mode simulations are short and small; several realism components are correctly marked Not evaluated.
- Latency is discretized into clock steps rather than modeled at exchange-gateway nanosecond resolution.
- Circuit breakers use a simple reference-price threshold.
- POV observes recent synthetic volume; a production execution model would use more detailed volume forecasting and venue state.
- Only one synthetic venue and three equities are supported.
- No commercial or proprietary order-book data is included.
- Results do not establish profitability, safety, or fitness for live trading.
- This is research and testing infrastructure, not investment advice.
