# Build Week provenance

This repository combines a protected pre-existing milestone, reusable research infrastructure, and new Build Week Arena work. The boundaries below prevent the submission from presenting every file as newly created for the final product.

## Protected Market Fuzzer milestone

```text
tag:    market-fuzzer-milestone-1
commit: 496fcc197719cf84a5fcb64b027e338caef2c3ab
```

At that tag, the repository already contained the compact deterministic POV harness, bounded stress search, counterexample minimization, replay, corrected-strategy comparison, regression fixtures, CLI, browser workflow, and supporting tests. That milestone is preserved at `/market-fuzzer`; Arena work does not rewrite its history or claim it was created in the final pass.

## Pre-existing research infrastructure

The synthetic-world, agent, matching-engine, calibration, experiment, analytics, and artifact modules predate the final Execution Challenge integration. They are reused infrastructure:

```text
app/world/       app/agents/       app/exchange/
app/calibration/ app/experiments/  app/analytics/
app/simulation.py
```

Reuse is a product-strength and an explicit provenance fact. The submission does not claim that the broader exchange or every research module was authored solely for the final Arena milestone.

## New Quant Challenge Arena sequence

The post-milestone range is:

```text
496fcc1..HEAD
```

Important checkpoints:

| Commit/range | Work attributable to the Arena product |
| --- | --- |
| `7cdafc7` | Grounded analyst and judge-facing release path |
| `5a0f943` | Bounded API inputs and model selection |
| `4f01390` | Initial education-oriented Quant Challenge Arena workflow |
| `c7abece` | Execution Arena connected to the synthetic exchange |
| `f95c7af` | Hidden-world route repair, signed demo sessions, strict execution-policy contract, fill-derived participation, and research packet |
| `f95c7af..HEAD` | SQLite lifecycle/audit persistence, complete order and latency evidence, released-view controls, GPT-5.6 execution feedback/evals, replay/rank UI, Playwright E2E, Docker health smoke, and final documentation alignment |

The final SHA should be copied from `git rev-parse HEAD` into the Devpost submission after the last verified commit. This document intentionally does not invent a future commit ID.

## New execution integration

The final Arena-specific implementation includes:

- a strict versioned declarative policy schema and common execution adapter;
- phase-aware `/api/arena/execution/...` routes and a public/hidden visibility contract;
- server-generated, resumable signed demo identities with an instructor-code gate and separately gated test-auth bypass;
- strategy-level order, fill, participation, inventory, impact, and latency evidence from the exchange;
- immutable public/hidden evaluation matrices and score decomposition;
- SQLite challenges and hidden-world manifests, sessions, practice runs, submissions, evaluations, leaderboard snapshots, qualitative challenge-design drafts, persisted feedback, phase history, and audit events, with atomic state/audit writes and transactional quota enforcement;
- synchronized replay, public-to-hidden rank movement, released heatmap, and evidence tables;
- grounded GPT-5.6 structured analysis over release-safe overall/intent aggregates plus public-trace IDs, persisted report recovery, qualitative-only design drafts, and an explicit no-key deterministic path;
- security, metamorphic, persistence, GPT, browser, and container verification.

## Codex and GPT-5.6

Codex was used to inspect the existing system, research bounded design references, implement and test the integration, run browser/container verification, and align product documentation. The final Devpost entry must add the required Codex `/feedback` session ID supplied by the participant.

GPT-5.6 is part of the running product only as a schema-constrained challenge designer/evidence analyst. Deterministic code remains authoritative. CI does not call a live model; structured model responses are mocked, and no-key fallback is first-class.

## Not imported

No ABIDES, ABIDES-Gym, HftBacktest, JAX-LOB, EvalAI, or Codabench code is vendored or added as a runtime dependency. No proprietary market data, participant records, institutional authentication, private Fenrix/Zion assets, secrets, or brokerage code was copied into this repository.

## Submission checklist

Before final Devpost submission, record:

- final repository SHA and successful GitHub Actions run;
- public repository URL and any public demo URL;
- sub-three-minute video URL;
- final screenshots;
- Codex `/feedback` session ID.
