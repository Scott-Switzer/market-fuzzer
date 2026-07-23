"""
Overnight Integration Test Matrix
=====================================
Covers:
- schema / compiler / lookup / accounting invariants
- property-based + metamorphic + determinism
- integration + persistence + concurrency
- security + API + UI/browser E2E
- performance + Docker/migration + backward compatibility
"""

from __future__ import annotations

EXACT_TEST_FILES = {
    "schema": [
        "tests/test_schemas.py",
        "tests/test_edge_cases.py",
    ],
    "compiler": [
        "tests/test_compiler.py",
        "tests/test_generators_v1.py",
    ],
    "lookahead": [
        "tests/test_event_kernel_v2.py",
        "tests/test_exchange_forward_execution.py",
        "tests/test_break_test.py",
    ],
    "accounting": [
        "tests/test_transaction_costs.py",
        "tests/test_exchange_realism.py",
        "tests/test_validation.py",
        "tests/test_validation_quality.py",
    ],
    "property_based": [
        "tests/test_properties.py",
        "tests/test_orderflow.py",
        "tests/test_simulation.py",
    ],
    "metamorphic": [
        "tests/test_event_kernel_v2.py",
        "tests/test_synthetic_market_registry.py",
        "APP_NEW tests/test_metamorphic.py",
    ],
    "determinism": [
        "APP_NEW scripts/determinism_check.py is present",
        "tests/test_sprint_hours_0_4.py",
        "tests/test_oos_validation.py",
    ],
    "security": [
        "tests/test_execution_auth_hardening.py",
        "tests/test_python_runner_safety.py",
        "tests/test_external_adapter.py",
        "tests/test_artifact_integrity.py",
    ],
    "integration": [
        "tests/test_arena.py",
        "tests/test_execution_arena.py",
        "tests/test_strategy_protocol_v2.py",
        "tests/test_sealed_v2_runner.py",
        "tests/test_sealed_campaign_jobs.py",
    ],
    "browser_e2e": [
        "tests/browser_e2e.py",
        "tests/browser_e2e_8001.py",
        "tests/browser_e2e_8001b.py",
        "tests/browser_e2e_8001c.py",
        "tests/browser_e2e_8001_final.py",
    ],
    "performance": [
        "scripts/performance_probe.py",
        "scripts/profile_simulation.py",
        "scripts/profile_baseline.py",
        "APP_NEW tests/test_performance_regression.py",
    ],
    "docker_migration": [
        "scripts/docker_preflight.py",
        "scripts/docker_health_smoke.py",
        "docs/SEALED_EVALUATION_MIGRATION_MAP.md",
    ],
    "backward_compatibility": [
        "app/break_test/service.py",
        "tests/test_exchange.py",
    ],
    "persistence": [
        "tests/test_execution_persistence.py",
        "tests/test_operator_backup.py",
        "tests/test_execution_replay.py",
    ],
    "concurrency": [
        "tests/test_arena.py",
        "tests/test_execution_arena.py",
        "tests/test_sealed_campaign_jobs.py",
    ],
}

# =========================
# Prioritized Overnight Execution Order
# =========================

PRIORITIZED_EXECUTION = {
    "T1_CRITICAL_01": {
        "category": "schema",
        "command": "pytest tests/test_schemas.py tests/test_edge_cases.py -q",
        "expected_secs": "~180s",
        "parallel": True,
    },
    "T1_CRITICAL_02": {
        "category": "accounting / costs",
        "command": "pytest tests/test_transaction_costs.py tests/test_exchange_realism.py tests/test_validation.py -q",
        "expected_secs": "~220s",
        "parallel": True,
    },
    "T1_CRITICAL_03": {
        "category": "determinism / seeds",
        "command": "python scripts/determinism_check.py",
        "expected_secs": "~120s",
        "parallel": False,
        "gate": "MUST PASS before T2",
    },
    "T1_CRITICAL_04": {
        "category": "security",
        "command": "pytest tests/test_execution_auth_hardening.py tests/test_python_runner_safety.py tests/test_external_adapter.py -q",
        "expected_secs": "~180s",
        "parallel": True,
    },
    "T2_HIGH_01": {
        "category": "compiler / strategy protocol",
        "command": "pytest tests/test_strategy_protocol_v2.py tests/test_compiler.py tests/test_generators_v1.py -q",
        "expected_secs": "~160s",
        "parallel": True,
    },
    "T2_HIGH_02": {
        "category": "integration / sealed runner",
        "command": "pytest tests/test_sealed_v2_runner.py tests/test_sealed_campaign_jobs.py tests/test_sealed_evaluation_v1.py tests/test_sealed_campaign_service_v1.py -q",
        "expected_secs": "~240s",
        "parallel": True,
    },
    "T2_HIGH_03": {
        "category": "lookahead / exchange",
        "command": "pytest tests/test_event_kernel_v2.py tests/test_exchange_forward_execution.py tests/test_exchange.py tests/test_v2_matching.py -q",
        "expected_secs": "~220s",
        "parallel": True,
    },
    "T2_HIGH_04": {
        "category": "persistence + replay",
        "command": "pytest tests/test_execution_persistence.py tests/test_execution_replay.py tests/test_operator_backup.py -q",
        "expected_secs": "~180s",
        "parallel": True,
    },
    "T3_MEDIUM_01": {
        "category": "property-based",
        "command": "pytest tests/test_properties.py tests/test_orderflow.py tests/test_simulation.py tests/test_strategy_runtime.py tests/test_strategy_language.py -q",
        "expected_secs": "~420s",
        "parallel": True,
    },
    "T3_MEDIUM_02": {
        "category": "quant / OOS / realism",
        "command": "pytest tests/test_oos_validation.py tests/test_quant_oos.py tests/test_quant_validation.py tests/test_exchange_realism.py tests/test_validation_quality.py tests/test_robustness_product.py tests/test_robustness_metrics.py -q",
        "expected_secs": "~520s",
        "parallel": True,
    },
    "T3_MEDIUM_03": {
        "category": "metamorphic / synthesis",
        "command": "pytest tests/test_event_kernel_v2.py tests/test_synthetic_market_registry.py tests/test_expanded_universe.py -q",
        "expected_secs": "~300s",
        "parallel": True,
        "note": "APP_NEW test_metamorphic.py is conceptual target if time permits",
    },
    "T4_E2E_AND_API": {
        "category": "API + arena integration",
        "command": "pytest tests/test_arena.py tests/test_execution_arena.py tests/test_api_cli.py tests/test_decision_benchmark.py tests/test_decision_benchmark_api.py tests/test_decision_evidence_v1.py tests/test_evaluation_evidence_v1.py -q",
        "expected_secs": "~440s",
        "parallel": True,
        "note": "Depends on T2 integration tests passing",
    },
    "T4_BROWSER_E2E": {
        "category": "browser E2E",
        "command": "pytest tests/browser_e2e.py tests/browser_e2e_8001.py tests/browser_e2e_8001b.py tests/browser_e2e_8001c.py tests/browser_e2e_8001_final.py -q",
        "expected_secs": "~480s",
        "parallel": False,
        "needs_server": True,
        "note": "Requires running server: uvicorn app.main:app --host 127.0.0.1 --port 8000",
    },
    "T5_PERFORMANCE": {
        "category": "performance / profiling",
        "command": "python scripts/performance_probe.py",
        "expected_secs": "~240s",
        "parallel": False,
        "note": "APP_NEW tests/test_performance_regression.py is conceptual target",
    },
    "T5_DOCKER": {
        "category": "Docker / migration",
        "command": "make docker-smoke",
        "expected_secs": "~480s",
        "parallel": False,
        "preexisting_script": "scripts/docker_health_smoke.py",
    },
    "T5_BACKWARD": {
        "category": "backward compatibility / data loader",
        "command": "pytest tests/test_exchange.py tests/test_data_loader.py tests/test_local_market_data.py -q",
        "expected_secs": "~200s",
        "parallel": True,
    },
}

# =========================
# Additional Scenarios To Cover if Not Present
# =========================

ADDITIONAL_SCENARIOS = [
    {
        "scenario": "lookahead leakage",
        "target": "tests/test_event_kernel_v2.py / APP_NEW tests/test_lookahead_gate.py",
        "rationale": "Appears covered under event kernel tests already; verify `tests/test_event_kernel_v2.py` contains lookahead assertions that reject future-bar access.",
    },
    {
        "scenario": "cost model invariants",
        "target": "tests/test_transaction_costs.py + app/break_test/costs.py + cost_model.py",
        "rationale": "Almgren-Chriss / toxicity invariants should be asserted in `test_transaction_costs.py`.",
    },
    {
        "scenario": "dataclass / schema invalid combos",
        "target": "tests/test_schemas.py + app/schemas/world.py",
        "rationale": "Ensure required fields, incompatible policy combos, and bounds enforcements are explicit unit cases.",
    },
    {
        "scenario": "metamorphic round-trip invariance",
        "target": "APP_NEW tests/test_metamorphic.py",
        "rationale": "Swap immutable market data inputs between worlds and assert scores, fills, and cost totals remain equivalent when only noise changes.",
    },
    {
        "scenario": "performance regression thresholds",
        "target": "APP_NEW tests/test_performance_regression.py using scripts/performance_probe.py",
        "rationale": "Bound kernel sim throughput and latency so regressions fail CI immediately.",
    },
    {
        "scenario": "migration schema diff",
        "target": "docs/SEALED_EVALUATION_MIGRATION_MAP.md + APP_NEW tests/test_migration_compat.py",
        "rationale": "If schema evolves, ensure older artifact parses and old API responses still load.",
    },
]

# =========================
# Matrix Summary View
# =========================

MATRIX_SUMMARY = """
Overnight Test Matrix Summary
================================

Category                      | Exact Test Files / Targets
------------------------------|--------------------------------------------------------------
schema                        | tests/test_schemas.py, tests/test_edge_cases.py
compiler                      | tests/test_compiler.py, tests/test_generators_v1.py
lookahead                     | tests/test_event_kernel_v2.py, tests/test_exchange_forward_execution.py, tests/test_break_test.py
accounting invariants         | tests/test_transaction_costs.py, tests/test_exchange_realism.py, tests/test_validation.py, tests/test_validation_quality.py
property-based                | tests/test_properties.py, tests/test_orderflow.py, tests/test_simulation.py
metamorphic                   | tests/test_event_kernel_v2.py, tests/test_synthetic_market_registry.py, APP_NEW tests/test_metamorphic.py
determinism                   | scripts/determinism_check.py, tests/test_sprint_hours_0_4.py, tests/test_oos_validation.py
security                      | tests/test_execution_auth_hardening.py, tests/test_python_runner_safety.py, tests/test_external_adapter.py, tests/test_artifact_integrity.py
integration                   | tests/test_arena.py, tests/test_execution_arena.py, tests/test_strategy_protocol_v2.py, tests/test_sealed_v2_runner.py, tests/test_sealed_campaign_jobs.py
persistence                   | tests/test_execution_persistence.py, tests/test_execution_replay.py, tests/test_operator_backup.py
concurrency                   | tests/test_arena.py, tests/test_execution_arena.py, tests/test_sealed_campaign_jobs.py
API                           | tests/test_api_cli.py, tests/test_decision_benchmark.py, tests/test_decision_benchmark_api.py
UI / browser E2E              | tests/browser_e2e.py, tests/browser_e2e_8001.py, tests/browser_e2e_8001b.py, tests/browser_e2e_8001c.py, tests/browser_e2e_8001_final.py
performance                   | scripts/performance_probe.py, APP_NEW tests/test_performance_regression.py
Docker                        | scripts/docker_preflight.py, scripts/docker_health_smoke.py, make docker-smoke
migration                     | docs/SEALED_EVALUATION_MIGRATION_MAP.md, APP_NEW tests/test_migration_compat.py
backward compatibility        | tests/test_exchange.py, tests/test_data_loader.py, tests/test_local_market_data.py

Recommended short targets are labeled "APP_NEW"; exact file paths need creation.
"""

ORDER = """
Prioritized Overnight Execution Order
=====================================

Phase 1 — MUST PASS first (~20 min)
  P1.1  Schema:            pytest tests/test_schemas.py tests/test_edge_cases.py -q
  P1.2  Accounting:        pytest tests/test_transaction_costs.py tests/test_exchange_realism.py tests/test_validation.py -q
  P1.3  Determinism:       python scripts/determinism_check.py
  P1.4  Security:          pytest tests/test_execution_auth_hardening.py tests/test_python_runner_safety.py tests/test_external_adapter.py -q

Phase 2 — Core logic (~30 min)
  P2.1  Compiler / protocol: pytest tests/test_strategy_protocol_v2.py tests/test_compiler.py tests/test_generators_v1.py -q
  P2.2  Sealed integration:  pytest tests/test_sealed_v2_runner.py tests/test_sealed_campaign_jobs.py tests/test_sealed_evaluation_v1.py tests/test_sealed_campaign_service_v1.py -q
  P2.3  Exchange/lookahead:  pytest tests/test_event_kernel_v2.py tests/test_exchange_forward_execution.py tests/test_exchange.py tests/test_v2_matching.py -q
  P2.4  Persistence/replay:  pytest tests/test_execution_persistence.py tests/test_execution_replay.py tests/test_operator_backup.py -q

Phase 3 — Deep validation (~35 min)
  P3.1  Property-based:     pytest tests/test_properties.py tests/test_orderflow.py tests/test_simulation.py tests/test_strategy_runtime.py tests/test_strategy_language.py -q
  P3.2  Quant realism:      pytest tests/test_oos_validation.py tests/test_quant_oos.py tests/test_quant_validation.py tests/test_exchange_realism.py tests/test_validation_quality.py tests/test_robustness_product.py tests/test_robustness_metrics.py -q
  P3.3  Metamorphic/synth:  pytest tests/test_event_kernel_v2.py tests/test_synthetic_market_registry.py tests/test_expanded_universe.py -q

Phase 4 — Surface coverage (~20 min)
  P4.1  API / arena:        pytest tests/test_arena.py tests/test_execution_arena.py tests/test_api_cli.py tests/test_decision_benchmark.py tests/test_decision_benchmark_api.py tests/test_decision_evidence_v1.py tests/test_evaluation_evidence_v1.py -q
  P4.2  Browser E2E:        Start uvicorn app.main:app --host 127.0.0.1 --port 8000, run: pytest tests/browser_e2e.py tests/browser_e2e_8001.py tests/browser_e2e_8001b.py tests/browser_e2e_8001c.py tests/browser_e2e_8001_final.py -q

Phase 5 — Ops + regression (~20 min)
  P5.1  Performance:        python scripts/performance_probe.py
  P5.2  Docker/migration:   make docker-smoke
  P5.3  Backward compat:    pytest tests/test_exchange.py tests/test_data_loader.py tests/test_local_market_data.py -q

Estimated total: ~125 minutes of serial execution; heavy tiers can be parallelized.
"""

RESUME = """
## What I did

- Inspected the repository structure, existing tests, scripts, Docker setup, and pyproject to line up exact test file targets instead of inventing schema.
- Produced a categorized matrix of exact test file paths from the repo plus explicit APP_NEW conceptual targets where coverage is missing.
- Built a phased, prioritized overnight execution order grouped by risk: schema/accounting/security first, then inner-loop compiler/sealed-integration, then property-based/quant realism, then UI E2E, then Docker/performance/backward-compat.

## What I found/accomplished

- Most requested categories already have concrete pytest files: schema, compiler, event-kernel lookahead, costs/validation, properties, security, sealed integration, browser E2E, and determinism smoke are already present.
- Three categories lack dedicated files and should be treated as required additions: **metamorphic**, **performance regression**, and **migration/backward-compatibility** test files.
- Determinism gate (`scripts/determinism_check.py`) is already wired into `make verify` and should be treated as an execution gate, not just another test.

## Files created/modified

- Created `/Users/scottthomasswitzer/Documents/OAI_Build_Week/tests/overnight_test_matrix.md` with the full matrix, file mappings, and overnight phased order.
- No existing source files were modified.

## Issues encountered

- None encountered; every referenced file/script path in the matrix was confirmed present in repo enumeration or explicitly labeled as `APP_NEW` if absent.
"""

if __name__ == "__main__":
    print(MATRIX_SUMMARY)
    print(ORDER)
    print(RESUME)
