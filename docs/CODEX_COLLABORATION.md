# Codex collaboration

Codex implemented the repository baseline and the Market Fuzzer product layer: a compact deterministic POV harness, safety properties, targeted adverse search, minimization, replay, exact fragile/corrected comparison, fixture validation, CLI replay, API regression-suite execution, browser workflow, tests, and documentation. The repository also retains broader synthetic-world and exact-exchange research infrastructure, but that is not silently claimed as the product backend.

The developer chose the final product direction: a developer tool that finds reproducible market conditions violating an execution strategy’s declared safety properties. The deterministic harness is deliberately bounded so the tutorial workflow can be tested end to end without overstating market fidelity.

Outputs were reviewed through unit and integration tests, deterministic replay, artifact-hash verification, API smoke tests, and browser workflow checks. Failures were repaired rather than suppressed, including the initial zero-depth snapshot caused by delayed market-maker quote arrival.

Primary core-work session: **ADD FINAL `/feedback` CODEX SESSION ID BEFORE SUBMISSION.**

GPT-5.6's product role is distinct from Codex: the optional online compiler converts a world request into strict structured data and discloses assumptions. The simulator remains deterministic and does not ask the model to create prices or fills.
# Rebuild handoff

Keep the deterministic exchange and product adapter coupled through tests. Do not expand browser execution to arbitrary user code; use built-in strategy adapters and fixtures for the demonstration workflow.
