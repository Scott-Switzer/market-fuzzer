# Product workflow

Market Fuzzer is a developer tool for adverse-condition testing of execution strategies. The primary workflow is:

```text
Strategy → Safety Properties → Baseline → Break My Strategy
→ Counterexample → Minimize → Fix and Retest → Regression Test
```

## What the user provides

The no-key product includes a fragile POV tutorial and a corrected POV implementation. The parent order, duration, participation cap, and latency are editable through the strategy form. Arbitrary uploaded Python is intentionally out of scope.

## What the system tests

The user enables explicit requirements such as minimum completion, maximum shortfall, maximum realized participation, no halt orders, and maximum remaining inventory. A baseline runs first. The bounded search then varies liquidity, latency, forced selling, and related synthetic conditions using deterministic seeds.

The tutorial search targets participation specifically. A candidate is retained only when the targeted property fails under the required seed rule. Minimization moves each dimension toward its normal value, recomputes evidence for every accepted trial, and retains a scenario only when the target still reproduces and severity does not increase.

## What the user receives

- A measured violation with threshold, observed value, margin, and first violation time.
- A minimized scenario with its own metrics, property rows, seed outcomes, replay, and scenario hash.
- A separately evaluated passing neighbor.
- A corrected-strategy comparison using the exact minimized scenario, parent-order configuration, properties, and seeds.
- YAML and JSON regression fixtures plus a CLI command.

The deterministic engine owns every verdict. AI suggestions are clearly labelled as hypotheses or interpretation, never as measured results.

## Evidence boundary

The result proves software behavior within the configured compact deterministic synthetic harness. It does not prove profitability, market prediction, production capacity, institutional calibration, live-trading safety, or regulatory compliance.
