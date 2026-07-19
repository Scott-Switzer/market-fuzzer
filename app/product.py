"""Compact deterministic Market Fuzzer POV test harness (not the full exchange)."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from app.evaluation import adaptive_diagnostic_evidence

STORE = Path(os.getenv("MARKET_FUZZER_ARTIFACT_ROOT", "artifacts/market_fuzzer")).expanduser()
STRATEGIES = {
    "pov_fragile": {
        "name": "Fragile POV (tutorial)",
        "type": "POV",
        "version": "built-in-1",
        "description": "Intentionally defective: it sizes from delayed volume and ignores pending orders.",
        "defaults": {
            "side": "buy",
            "asset": "ACME",
            "quantity": 50000,
            "max_participation": 10,
            "duration": 20,
            "latency_ms": 10,
        },
    },
    "pov": {
        "name": "Corrected POV",
        "type": "POV",
        "version": "built-in-2",
        "description": "Conservatively caps submitted and pending quantity against current volume.",
        "defaults": {
            "side": "buy",
            "asset": "ACME",
            "quantity": 50000,
            "max_participation": 10,
            "duration": 20,
            "latency_ms": 10,
        },
    },
}
DEFAULT_PROPERTIES = [
    {
        "id": "completion",
        "name": "Minimum completion",
        "description": "Percent filled by completion.",
        "units": "%",
        "threshold": 95,
        "operator": ">=",
    },
    {
        "id": "shortfall",
        "name": "Maximum implementation shortfall",
        "description": "Deterministic execution-cost proxy.",
        "units": "bps",
        "threshold": 20,
        "operator": "<=",
    },
    {
        "id": "participation",
        "name": "Maximum participation",
        "description": "Largest realized market-volume share.",
        "units": "%",
        "threshold": 12,
        "operator": "<=",
    },
    {
        "id": "halt",
        "name": "No orders during a halt",
        "description": "Orders while halted.",
        "units": "orders",
        "threshold": 0,
        "operator": "<=",
    },
    {
        "id": "remaining",
        "name": "Maximum remaining inventory",
        "description": "Unfilled parent order.",
        "units": "%",
        "threshold": 5,
        "operator": "<=",
    },
]


def stable_id(prefix: str, data: Any) -> str:
    return (
        f"{prefix}_{hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:10]}"
    )


def scenario_hash(s: dict) -> str:
    return stable_id("scenario", s)


def severity(s: dict) -> dict:
    c = {
        "liquidity": 1 - s.get("liquidity", 1),
        "volatility": (s.get("volatility", 1) - 1) / 3,
        "latency": max(0, s.get("latency_ms", 10) - 10) / 90,
        "forced_flow": s.get("forced_seller", 0) / 50000,
        "spread": (s.get("spread", 1) - 1) / 3,
        "replenishment": 1 - s.get("replenishment", 1),
    }
    return {
        "policy_version": "severity-2.0",
        "weights": {k: 1 for k in c},
        "components": {k: round(v, 3) for k, v in c.items()},
        "score": round(sum(c.values()), 3),
    }


def _strategy(strategy):
    return strategy.get("parameters", strategy.get("defaults", {})), strategy.get("id") == "pov_fragile"


def evaluate(strategy: dict, scenario: dict, properties: list[dict], seed: int) -> dict:
    p, fragile = _strategy(strategy)
    qty = p.get("quantity", 50000)
    cap = p.get("max_participation", 10) / 100
    remaining = float(qty)
    pending: list[tuple[int, float]] = []
    max_part = 0
    filled = 0.0
    cost = 0.0
    timeline = []
    latency = max(1, round(scenario.get("latency_ms", p.get("latency_ms", 10)) / 10))
    for t in range(20):
        base = 30000 + (seed % 7) * 300
        contraction = scenario.get("liquidity", 1) * (
            0.75 if t >= 9 and scenario.get("forced_seller", 0) else 1
        )
        volume = max(1000, base * contraction)
        forced = scenario.get("forced_seller", 0) / 8 if 9 <= t < 17 else 0
        due = [x for x in pending if x[0] <= t]
        pending = [x for x in pending if x[0] > t]
        submitted = sum(x[1] for x in due)
        executable = max(0, volume - forced) * (0.8 if fragile else 1.0)
        # Corrected POV applies its hard cap at fill time too, so delayed orders
        # cannot consume more than the contemporaneous market-volume budget.
        if not fragile:
            executable = min(executable, cap * volume)
        fill = min(submitted, executable, remaining)
        remaining -= fill
        filled += fill
        part = fill / volume
        max_part = max(max_part, part)
        cost += part * scenario.get("spread", 1) * 2 + max(0, scenario.get("volatility", 1) - 1) * 0.4
        observed = volume if not fragile else max(1000, base * scenario.get("liquidity", 1))
        budget = max(0, cap * observed - (0 if fragile else sum(x[1] for x in pending)))
        target = min(remaining, budget)
        pending.append((t + latency, target))
        timeline.append(
            {
                "step": t,
                "market_volume": round(volume),
                "observed_volume": round(observed),
                "submitted": round(target),
                "filled": round(fill),
                "pending": round(sum(x[1] for x in pending)),
                "participation": round(part * 100, 2),
                "depth": round(volume),
                "spread_bps": round(4 * scenario.get("spread", 1), 1),
                "price": round(100 + cost * 0.01, 3),
                "progress": round(filled / qty * 100, 2),
                "forced_flow": round(forced),
                "failure": part > cap,
            }
        )
    metrics = {
        "completion": round(filled / qty * 100, 2),
        "shortfall": round(cost, 2),
        "participation": round(max_part * 100, 2),
        "halt": 0,
        "remaining": round(remaining / qty * 100, 2),
    }
    rows = []
    for prop in properties:
        value = metrics[prop["id"]]
        passed = value >= prop["threshold"] if prop["operator"] == ">=" else value <= prop["threshold"]
        rows.append(
            {
                **prop,
                "observed": value,
                "passed": passed,
                "margin": round(
                    (prop["threshold"] - value) if prop["operator"] == ">=" else (value - prop["threshold"]),
                    2,
                ),
                "first_violation_time": None
                if passed
                else next(
                    (
                        f"00:00:{x['step'] * 3:02d}"
                        for x in timeline
                        if (prop["id"] == "participation" and x["participation"] > prop["threshold"])
                    ),
                    "00:01:00",
                ),
                "evidence": ["deterministic-pov-harness", f"seed-{seed}"],
            }
        )
    return {
        "seed": seed,
        "scenario_hash": scenario_hash(scenario),
        "metrics": metrics,
        "properties": rows,
        "passed": all(x["passed"] for x in rows),
        "timeline": timeline,
    }


def _target_fail(runs, properties=None):
    """Count runs that violate the enabled participation property."""
    enabled = {p["id"] for p in (properties or DEFAULT_PROPERTIES)}
    if "participation" not in enabled:
        return 0
    return sum(any(p["id"] == "participation" and not p["passed"] for p in r["properties"]) for r in runs)


def _runs(strategy, s, p, seeds):
    return [evaluate(strategy, s, p, x) for x in seeds]


def _diagnostic_result(result: dict) -> dict:
    """Attach shared evidence without turning adaptive search into a primary score."""
    mechanism = str(result.get("violated_property", {}).get("id", "bounded_failure_search"))
    result["evaluation_evidence"] = adaptive_diagnostic_evidence(
        payload=result,
        mechanism=mechanism,
        limitation="This search inspects a strategy and minimizes a configured failure; it is not independently selected primary evaluation.",
    ).to_dict()
    return result


def run_search(strategy, properties, mode="quick"):
    seeds = [41, 42, 43] if mode == "quick" else list(range(41, 49))
    if "participation" not in {p["id"] for p in properties}:
        return _diagnostic_result(
            {
                "status": "complete",
                "found": False,
                "tested": 0,
                "message": "Enable the participation property to search for the POV tutorial defect.",
            }
        )
    candidates = [
        {
            "liquidity": liquidity,
            "volatility": 1,
            "latency_ms": lat,
            "forced_seller": f,
            "spread": 1,
            "replenishment": 1,
        }
        for liquidity in (0.9, 0.7, 0.55, 0.44)
        for lat in (20, 30, 40)
        for f in (0, 500, 5000)
    ]
    required_failures = 2 if mode == "quick" else 6
    qualifying = []
    for candidate in candidates:
        candidate_runs = _runs(strategy, candidate, properties, seeds)
        target_failures = _target_fail(candidate_runs, properties)
        if target_failures >= required_failures:
            qualifying.append((severity(candidate)["score"], candidate, candidate_runs))
    if not qualifying:
        return _diagnostic_result(
            {
                "status": "complete",
                "found": False,
                "tested": len(candidates),
                "message": "No reproducible participation failure within bounds.",
            }
        )
    _, original, runs = min(qualifying, key=lambda x: (x[0], json.dumps(x[1], sort_keys=True)))
    minimized = dict(original)
    trace = []
    step = 0
    for key, levels in (
        ("liquidity", (0.55, 0.7, 0.9, 1)),
        ("latency_ms", (30, 20, 10)),
        ("forced_seller", (0,)),
    ):
        for value in levels:
            step += 1
            trial = {**minimized, key: value}
            rr = _runs(strategy, trial, properties, seeds)
            before = severity(minimized)
            after = severity(trial)
            target_failures = _target_fail(rr, properties)
            severity_ok = after["score"] <= before["score"]
            reproduction_ok = target_failures >= required_failures
            ok = reproduction_ok and severity_ok
            trace.append(
                {
                    "step": step,
                    "dimension": key,
                    "old_value": minimized[key],
                    "trial_value": value,
                    "accepted": ok,
                    "reason": "target failure reproduced and severity moved toward baseline"
                    if ok
                    else "target failure or severity condition not met",
                    "severity_before": before,
                    "severity_after": after,
                    "seeds_failed": target_failures,
                    "seeds_tested": seeds,
                }
            )
            if ok:
                minimized = trial
    final_runs = _runs(strategy, minimized, properties, seeds)
    neighbor = {**minimized, "liquidity": min(1, minimized["liquidity"] + 0.15)}
    neighbor_runs = _runs(strategy, neighbor, properties, seeds)
    # Only call it a passing neighbor when verified; otherwise use the next latency improvement.
    if not all(r["passed"] for r in neighbor_runs):
        neighbor = {**minimized, "latency_ms": max(10, minimized["latency_ms"] - 10)}
        neighbor_runs = _runs(strategy, neighbor, properties, seeds)
    if not all(r["passed"] for r in neighbor_runs):
        neighbor = {**minimized, "forced_seller": 0}
        neighbor_runs = _runs(strategy, neighbor, properties, seeds)
    if not all(r["passed"] for r in neighbor_runs):
        return _diagnostic_result(
            {
                "status": "complete",
                "found": False,
                "tested": len(candidates),
                "message": "A participation failure was found, but no verified passing neighbor exists within the search grid.",
            }
        )
    target = next(p for p in final_runs[0]["properties"] if p["id"] == "participation")
    fid = stable_id(
        "failure", {"strategy": strategy.get("id"), "scenario": minimized, "properties": properties}
    )
    return _diagnostic_result(
        {
            "id": fid,
            "status": "complete",
            "found": True,
            "tested": len(candidates),
            "scenario": original,
            "original_runs": runs,
            "minimized": minimized,
            "runs": final_runs,
            "minimization_trace": trace,
            "passing_neighbor": neighbor,
            "passing_neighbor_runs": neighbor_runs,
            "severity": severity(minimized),
            "violated_property": target,
            "reproduction": {
                "seeds_tested": seeds,
                "seeds_failed": _target_fail(final_runs, properties),
                "failure_rate": _target_fail(final_runs, properties) / len(seeds),
                "calibration_sets_tested": 1,
                "calibration_sets_failed": 1 if _target_fail(final_runs, properties) else 0,
            },
            "scenario_hash": scenario_hash(minimized),
            "passing_neighbor_scenario_hash": scenario_hash(neighbor),
        }
    )


def export_fixture(failure, strategy, properties):
    STORE.mkdir(parents=True, exist_ok=True)
    fixture = {
        "schema_version": "1.1",
        "case": {
            "id": failure["id"],
            "name": "stale_volume_participation",
            "created_at": datetime.now(UTC).isoformat(),
            "source_failure_id": failure["id"],
        },
        "scenario_hash": failure["scenario_hash"],
        "strategy": {
            "id": strategy["id"],
            "type": strategy["type"],
            "version": strategy.get("version", "built-in"),
            "parameters": strategy.get("parameters", strategy["defaults"]),
        },
        "market": {**failure["minimized"]},
        "seeds": failure["reproduction"]["seeds_tested"],
        "safety_properties": properties,
        "expected": {
            "result": "fail",
            "targeted_property": "participation",
            "targeted_result": "fail",
            "minimum_violation_margin": failure["violated_property"]["margin"],
        },
        "provenance": {"policy_versions": ["severity-2.0", "minimization-1.0"]},
    }
    path = STORE / f"{failure['id']}.yaml"
    fixture["provenance"]["reproduction_command"] = f"smw test {path}"
    path.write_text(yaml.safe_dump(fixture, sort_keys=False))
    path.with_suffix(".json").write_text(json.dumps(fixture, indent=2))
    return {"fixture": fixture, "yaml": str(path), "json": str(path.with_suffix(".json"))}
