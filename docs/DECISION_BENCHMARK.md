# Decision-change benchmark

The product now ships a deterministic benchmark that answers the customer question: “Did the stress result change what I would choose?”

`build_decision_change_benchmark()` runs the declared public/protected matrix, records the visible winner and protected robustness winner, and emits an artifact hash. In the canonical seed contract, Aggressive POV wins the visible objective while Guarded POV wins protected robustness, so the evidence-supported decision is to prefer Guarded POV for the stressed case.

This is a reproducible decision-evidence example inside Synthetic Market World. It is not a live-market validation, profitability result, or execution-safety guarantee.
