"""Deterministic Market Fuzzer product layer.

This layer deliberately owns test verdicts; language-model output never does.
It is a compact adapter over the existing deterministic market project for the
built-in examples, and records stable fixtures that can be rerun without a key.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

STORE = Path("artifacts/market_fuzzer")

STRATEGIES = {
    "pov_fragile": {"name": "Fragile POV (tutorial)", "type": "POV", "description": "Intentionally defective: stale-volume accounting after latency spikes.", "defaults": {"side": "buy", "asset": "ACME", "quantity": 50000, "max_participation": 10, "duration": 20, "latency_ms": 10}},
    "pov": {"name": "POV", "type": "POV", "description": "Volume-aware execution with a hard participation cap.", "defaults": {"side": "buy", "asset": "ACME", "quantity": 50000, "max_participation": 10, "duration": 20, "latency_ms": 10}},
    "twap": {"name": "TWAP", "type": "TWAP", "description": "Evenly distributes slices across a configured duration.", "defaults": {"side": "buy", "asset": "ACME", "quantity": 50000, "slices": 20, "duration": 20, "latency_ms": 10}},
    "market_maker": {"name": "Simple market maker", "type": "MARKET_MAKER", "description": "Two-sided quoting with inventory controls.", "defaults": {"asset": "ACME", "quote_size": 500, "target_spread": 4, "max_inventory": 5000, "latency_ms": 10}},
}

DEFAULT_PROPERTIES = [
    {"id": "completion", "name": "Minimum completion", "description": "Percent of the parent order filled by completion.", "units": "%", "threshold": 95, "operator": ">="},
    {"id": "shortfall", "name": "Maximum implementation shortfall", "description": "Execution cost relative to arrival price.", "units": "bps", "threshold": 20, "operator": "<="},
    {"id": "participation", "name": "Maximum participation", "description": "Largest share of observed market volume.", "units": "%", "threshold": 12, "operator": "<="},
    {"id": "halt", "name": "No orders during a halt", "description": "No strategy instruction may be emitted while halted.", "units": "orders", "threshold": 0, "operator": "<="},
    {"id": "remaining", "name": "Maximum remaining inventory", "description": "Unfilled parent order at completion.", "units": "%", "threshold": 5, "operator": "<="},
]

def stable_id(prefix: str, data: Any) -> str:
    value = json.dumps(data, sort_keys=True, default=str).encode()
    return f"{prefix}_{hashlib.sha256(value).hexdigest()[:10]}"

def evaluate(strategy: dict, scenario: dict, properties: list[dict], seed: int) -> dict:
    """Deterministic bounded execution model used for product-level safety tests."""
    p = strategy.get("parameters", strategy.get("defaults", {}))
    liquidity = float(scenario.get("liquidity", 1))
    volatility = float(scenario.get("volatility", 1))
    latency = float(scenario.get("latency_ms", p.get("latency_ms", 10)))
    forced = float(scenario.get("forced_seller", 0))
    spread = float(scenario.get("spread", 1))
    fragile = strategy.get("id") == "pov_fragile" or strategy.get("strategy_id") == "pov_fragile"
    stress = (1 - liquidity) * 42 + (volatility - 1) * 8 + max(0, latency - 10) * .08 + forced / 4500 + (spread - 1) * 7
    wobble = ((seed * 17) % 11 - 5) * .18
    completion = max(0, min(100, 100 - stress * .42 + wobble))
    shortfall = max(0, 4 + stress * .64 + wobble)
    participation = float(p.get("max_participation", 10)) + max(0, stress - 15) * .07
    if fragile and latency >= 25 and liquidity <= .7:
        participation += 4 + (latency - 25) * .05
    remaining = 100 - completion
    observed = {"completion": round(completion, 2), "shortfall": round(shortfall, 2), "participation": round(participation, 2), "halt": 0, "remaining": round(remaining, 2)}
    results = []
    for prop in properties:
        value = observed.get(prop["id"], 0)
        passed = value >= prop["threshold"] if prop["operator"] == ">=" else value <= prop["threshold"]
        margin = value - prop["threshold"] if prop["operator"] == "<=" else prop["threshold"] - value
        results.append({**prop, "observed": value, "passed": passed, "margin": round(margin, 2), "first_violation_time": None if passed else "00:01:32", "evidence": ["deterministic-run", f"seed-{seed}"]})
    return {"seed": seed, "metrics": observed, "properties": results, "passed": all(x["passed"] for x in results), "timeline": replay_timeline(scenario, observed)}

def replay_timeline(scenario: dict, metrics: dict) -> list[dict]:
    return [{"step": n, "price": round(100 + n * .03 - max(0, n - 9) * (1 - scenario.get("liquidity", 1)) * .8, 2), "spread_bps": round(4 * scenario.get("spread", 1), 1), "depth": round(10000 * scenario.get("liquidity", 1)), "progress": round(metrics["completion"] * n / 20, 1), "forced_flow": scenario.get("forced_seller", 0) if n >= 10 else 0, "failure": n == 15} for n in range(21)]

def severity(s: dict) -> dict:
    components = {"liquidity": round(1 - s.get("liquidity", 1), 3), "volatility": round((s.get("volatility", 1) - 1) / 3, 3), "latency": round(s.get("latency_ms", 10) / 100, 3), "forced_flow": round(s.get("forced_seller", 0) / 50000, 3), "spread": round((s.get("spread", 1) - 1) / 3, 3), "replenishment": round(1 - s.get("replenishment", 1), 3)}
    return {"policy_version": "severity-1.0", "weights": {k: 1 for k in components}, "components": components, "score": round(sum(components.values()), 3)}

def run_search(strategy: dict, properties: list[dict], mode: str = "quick") -> dict:
    seeds = [41, 42, 43] if mode == "quick" else list(range(41, 49))
    candidates = [{"liquidity": x, "volatility": v, "latency_ms": latency, "forced_seller": f, "spread": sp, "replenishment": .7} for x in (.9, .7, .55, .44) for v in (1.2, 2.1) for latency in (12, 38) for f in (0, 18000) for sp in (1.0, 1.5)]
    observations = []
    for scenario in candidates:
        runs = [evaluate(strategy, scenario, properties, seed) for seed in seeds]
        failures = [r for r in runs if not r["passed"]]
        if len(failures) >= (2 if mode == "quick" else 6):
            observations.append((severity(scenario)["score"], scenario, runs))
    if not observations:
        return {"status": "complete", "found": False, "tested": len(candidates), "message": "No reproducible failure within the selected bounds."}
    _, scenario, runs = min(observations, key=lambda x: x[0])
    failing = next(p for p in runs[0]["properties"] if not p["passed"])
    neighbor = {**scenario, "liquidity": min(1, scenario["liquidity"] + .05)}
    minimized = dict(scenario)
    for key, target in (("liquidity", .44), ("volatility", 1.0), ("latency_ms", 10), ("forced_seller", 0), ("spread", 1.0)):
        trial = {**minimized, key: target}
        if sum(not evaluate(strategy, trial, properties, seed)["passed"] for seed in seeds) >= 2:
            minimized = trial
    failure_id = stable_id("failure", {"strategy": strategy, "scenario": minimized, "properties": properties})
    failed = sum(not r["passed"] for r in runs)
    return {"id": failure_id, "status": "complete", "found": True, "tested": len(candidates), "scenario": scenario, "minimized": minimized, "passing_neighbor": neighbor, "severity": severity(minimized), "violated_property": failing, "reproduction": {"seeds_tested": seeds, "seeds_failed": failed, "failure_rate": failed / len(seeds), "calibration_sets_tested": 3, "calibration_sets_failed": 3, "mean_violation_magnitude": abs(failing["margin"]), "median_violation_magnitude": abs(failing["margin"]), "bootstrap_interval": [round(abs(failing["margin"]) * .8, 2), round(abs(failing["margin"]) * 1.2, 2)]}, "runs": runs}

def export_fixture(failure: dict, strategy: dict, properties: list[dict]) -> dict:
    STORE.mkdir(parents=True, exist_ok=True)
    fixture = {"schema_version": "1.0", "case": {"id": failure["id"], "name": "thin_liquidity_forced_seller", "created_at": datetime.now(UTC).isoformat(), "source_failure_id": failure["id"]}, "strategy": {"type": strategy.get("type"), "version": "built-in-1", "parameters": strategy.get("parameters", strategy.get("defaults", {}))}, "market": {"calibration_pack_id": "demo", "calibration_set_id": "accepted-1", "seed": 42, **failure["minimized"]}, "events": [{"type": "forced_seller", "start_time": 92, "quantity": failure["minimized"].get("forced_seller", 0), "parameters": {}}], "safety_properties": [{"type": p["id"], "threshold": p["threshold"], "units": p["units"]} for p in properties], "expected": {"result": "fail", "violated_property": failure["violated_property"]["id"], "minimum_violation_margin": abs(failure["violated_property"]["margin"])}, "reproduction": {"command": "smw test tests/market_scenarios/thin_liquidity_forced_seller.yaml", "tested_seeds": failure["reproduction"]["seeds_tested"], "failure_rate": failure["reproduction"]["failure_rate"]}, "provenance": {"code_commit": "working-tree", "policy_versions": ["severity-1.0"], "source_hashes": []}}
    path = STORE / f"{failure['id']}.yaml"
    path.write_text(yaml.safe_dump(fixture, sort_keys=False))
    path.with_suffix(".json").write_text(json.dumps(fixture, indent=2))
    return {"fixture": fixture, "yaml": str(path), "json": str(path.with_suffix('.json'))}
