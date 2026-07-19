# Synthetic Market World

Synthetic Market World is the enterprise product boundary for this repository:

> A governed synthetic market environment for strategy validation and adversarial stress testing.

The exchange, agent ecology, calibration, scenario interventions, replay, and
regression artifacts are the core platform. Quant Challenge Arena is a training
and assessment surface built on that platform. Market Fuzzer is the developer
failure-minimization surface.

## Current enterprise foundation

The World Registry now persists:

- versioned synthetic-world manifests;
- intended use and calibration references;
- asset universe and agent ecology declarations;
- scenario packs with allow-listed interventions;
- manifest hashes and audit events.

Create a world with `POST /api/enterprise/worlds`, inspect it with
`GET /api/enterprise/worlds/{world_id}`, and create a bounded scenario pack with
`POST /api/enterprise/scenario-packs`. These records describe inputs only;
deterministic application code remains authoritative for simulation outcomes.

## Product roadmap

1. World Registry: worlds, versions, calibration references, and provenance.
2. Scenario Studio: deterministic compilation of approved intervention packs.
3. Strategy Stress Lab: strategy versions, baseline/stress comparisons, sweeps,
   minimized failures, and regression suites.
4. Validation and Governance: calibration reports, fit-for-use verdicts,
   approvals, lineage, and exportable evidence packages.
5. Scale: resumable experiment jobs, parallel scenario cells, caching, and
   durable artifact storage.

## Claim boundary

Synthetic worlds are controlled counterfactual environments. They can provide
reproducible evidence about behavior inside a declared configuration; they do
not establish profitability, live-market fidelity, best execution, or
production readiness without separate validation and approval.
