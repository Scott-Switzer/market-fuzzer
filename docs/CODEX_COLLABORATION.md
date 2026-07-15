# Codex collaboration

Codex implemented the repository baseline, typed schemas, exact exchange, ledgers, synthetic world, agents, experiment runner, artifacts, analytics, compilers, API, CLI, browser experience, tests, and release documentation. It accelerated primary-source license research, architecture review, test design, implementation, failure repair, browser validation, and release auditing.

The developer chose the product direction: a firm-facing synthetic market-world engine with execution stress testing as its quantitative proof. The developer also required a complete synthetic world rather than a scenario-card mock. Codex converted those decisions into bounded architecture and evidence gates.

Outputs were reviewed through unit and integration tests, deterministic replay, artifact-hash verification, API smoke tests, and browser workflow checks. Failures were repaired rather than suppressed, including the initial zero-depth snapshot caused by delayed market-maker quote arrival.

Primary core-work session: **ADD FINAL `/feedback` CODEX SESSION ID BEFORE SUBMISSION.**

GPT-5.6's product role is distinct from Codex: the optional online compiler converts a world request into strict structured data and discloses assumptions. The simulator remains deterministic and does not ask the model to create prices or fills.
# Rebuild handoff

Keep the deterministic exchange and product adapter coupled through tests. Do not expand browser execution to arbitrary user code; use built-in strategy adapters and fixtures for the demonstration workflow.

