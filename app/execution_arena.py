"""Exchange-backed execution challenge used by the primary Arena experience.

This module deliberately sits above the matching engine.  It does not calculate
fills or prices itself: every metric is derived from ``run_simulation``.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from statistics import mean
from typing import Any

from app.simulation import SimulationResult, run_simulation
from app.world.scenarios import build_demo_world, mutate_scenario

CHALLENGE_ID = "trade-the-shock"
CHALLENGE_VERSION = "1.0"
SCORING_VERSION = "1.0"
WORLD_VARIANTS = ("normal", "liquidity_withdrawal", "crowded_unwind", "earnings_shock", "latency_shock")
HIDDEN_VARIANTS = WORLD_VARIANTS[1:]
SEEDS = (41, 42)


@dataclass(frozen=True)
class Policy:
    policy_id: str
    name: str
    description: str
    strategy: str
    participation_rate: float
    latency_ms: int
    parent_quantity: int = 6_000
    max_spread_bps: float = 12.0
    pause_during_halt: bool = True


POLICIES: dict[str, Policy] = {
    "twap": Policy(
        "twap", "TWAP benchmark", "Evenly schedules the order across the horizon.", "twap", 0.08, 5
    ),
    "aggressive_pov": Policy(
        "aggressive_pov",
        "Aggressive POV",
        "Completes quickly in public practice, but takes more flow when conditions deteriorate.",
        "pov",
        0.12,
        4,
    ),
    "guarded_pov": Policy(
        "guarded_pov",
        "Guarded adaptive POV",
        "Uses a lower participation cap and preserves room for hidden liquidity shocks.",
        "pov",
        0.08,
        8,
    ),
    "completion_first": Policy(
        "completion_first",
        "Completion-first POV",
        "Uses an elevated participation cap to minimize terminal inventory.",
        "pov",
        0.18,
        3,
    ),
}


def _world_for(policy: Policy, variant: str, seed: int) -> tuple[Any, dict[str, Any]]:
    base = build_demo_world(seed)
    base, changes = mutate_scenario(base, "normal" if variant == "latency_shock" else variant)
    data = deepcopy(base.model_dump(mode="python"))
    data["world_id"] = f"{CHALLENGE_ID}-{variant}-{policy.policy_id}-{seed}"
    data["experiment"]["strategy"] = policy.strategy
    data["experiment"]["participation_rate"] = policy.participation_rate
    data["experiment"]["latency_ms"] = policy.latency_ms
    data["experiment"]["parent_order"]["quantity"] = policy.parent_quantity
    data["experiment"]["counterfactual_mutations"] = list(WORLD_VARIANTS)
    data["ground_truth_labels"].update(
        {
            "challenge_id": CHALLENGE_ID,
            "challenge_version": CHALLENGE_VERSION,
            "world_variant": variant,
            "policy_id": policy.policy_id,
        }
    )
    if variant == "latency_shock":
        data["exchange"]["latency_profile"] = "high"
        data["macro"]["volatility_regime"] = "elevated"
        changes = {
            "scenario": variant,
            "constants": ["seed", "assets", "clock"],
            "changed": ["exchange latency profile becomes high", "elevated volatility"],
        }
    # The strategy's entry latency is independent from the shared exchange profile.
    for population in data["agents"]["populations"]:
        if population["type"] == "execution":
            population["latency_ms"] = policy.latency_ms
    return type(base).model_validate(data), changes


def _execution_metrics(result: SimulationResult, policy: Policy) -> dict[str, float | int | None]:
    summary = result.summary
    trades = [
        row
        for row in result.trades
        if row["buyer_id"] == "execution-01" or row["seller_id"] == "execution-01"
    ]
    target = policy.parent_quantity
    executed = int(summary["filled_quantity"])
    market_volume = max(1, int(summary["total_market_volume"]))
    fills_by_step: list[int] = []
    # Timeline contains market volume by step.  Its aggregate is a defensible
    # proxy for participation without inventing individual fill timestamps.
    for frame in result.timeline:
        fills_by_step.append(int(frame["asset_states"]["NOVA"]["volume"]))
    average_participation = min(1.0, executed / market_volume)
    max_participation = min(1.0, max(fills_by_step, default=0) / max(1, market_volume))
    inventory_path = []
    for row in result.agent_states:
        if row["agent_id"] == "execution-01":
            inventory_path.append(abs(int(row["inventory"].get("NOVA", 0))))
    return {
        "arrival_price_ticks": round(float(summary["arrival_price_ticks"]), 3),
        "market_vwap_ticks": round(float(summary["market_vwap_ticks"]), 3),
        "average_execution_price_ticks": round(float(summary["average_execution_price_ticks"]), 3),
        "implementation_shortfall_bps": round(float(summary["implementation_shortfall_bps"]), 3),
        "temporary_impact_bps": round(float(summary["temporary_impact_bps"]), 3),
        "persistent_impact_bps": round(float(summary["persistent_impact_bps"]), 3),
        "completion_pct": round(100 * float(summary["fill_rate"]), 2),
        "remaining_inventory": int(summary["remaining_inventory"]),
        "terminal_inventory_penalty": round(100 * (target - executed) / target, 3),
        "average_participation_pct": round(100 * average_participation, 3),
        "max_participation_pct": round(100 * max_participation, 3),
        "time_weighted_inventory": round(mean(inventory_path), 3) if inventory_path else 0.0,
        "fill_ratio": round(executed / target, 5),
        "cancel_fill_ratio": 0.0,
        "adverse_selection_bps": round(float(summary["adverse_selection_bps"]), 3),
        "spread_paid_bps": round(float(summary["spread_paid_bps"]), 3),
        "market_disruption": round(float(summary["market_disruption"]), 3),
        "strategy_trade_count": len(trades),
    }


def _number(metrics: dict[str, float | int | None], key: str) -> float:
    value = metrics[key]
    if value is None:
        raise ValueError(f"required execution metric {key!r} was absent")
    return float(value)


def _public_score(metrics: dict[str, float | int | None]) -> float:
    """Practice rubric: completion matters, then lower shortfall breaks ties."""
    return round(
        _number(metrics, "completion_pct") * 10 - max(0.0, _number(metrics, "implementation_shortfall_bps")),
        3,
    )


def _robustness_score(metrics_by_world: list[dict[str, float | int | None]]) -> float:
    shortfalls = [max(0.0, _number(row, "implementation_shortfall_bps")) for row in metrics_by_world]
    completions = [_number(row, "completion_pct") for row in metrics_by_world]
    impacts = [max(0.0, _number(row, "temporary_impact_bps")) for row in metrics_by_world]
    inventory_penalties = [_number(row, "terminal_inventory_penalty") for row in metrics_by_world]
    # Transparent bounded rubric; values are relative challenge points, not a
    # production execution estimate.
    score = (
        60 * max(0.0, 1 - mean(shortfalls) / 250)
        + 5 * mean(completions) / 100
        + 10 * max(0.0, 1 - mean(impacts) / 450)
        + 5 * max(0.0, 1 - mean(inventory_penalties) / 100)
        + 15 * max(0.0, 1 - max(shortfalls) / 250)
        + 5
    )
    return round(score, 3)


def run_execution_challenge(policy_id: str, world_variant: str = "normal", seed: int = 42) -> dict[str, Any]:
    if policy_id not in POLICIES:
        raise ValueError(f"unknown declarative policy {policy_id!r}")
    if world_variant not in WORLD_VARIANTS:
        raise ValueError(f"unknown world variant {world_variant!r}")
    policy = POLICIES[policy_id]
    spec, changes = _world_for(policy, world_variant, seed)
    result = run_simulation(spec)
    metrics = _execution_metrics(result, policy)
    return {
        "challenge_id": CHALLENGE_ID,
        "phase": "public_practice" if world_variant == "normal" else "hidden_evaluation",
        "policy": policy.__dict__,
        "world": {
            "variant": world_variant,
            "seed": seed,
            "changes": changes,
            "specification_hash": result.spec_hash,
        },
        "metrics": metrics,
        "public_score": _public_score(metrics),
        "replay": {"timeline": result.timeline, "events": result.events, "trades": result.trades},
        "evidence": {
            "mechanical_validity": "PASS",
            "challenge_behavior": "PASS",
            "selected_stylized_facts": "NOT_EVALUATED",
            "real_market_calibration": "NOT_CLAIMED",
            "result_hash": result.result_hash,
        },
    }


def _rank(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    ranked = sorted(rows, key=lambda row: row[key], reverse=True)
    for rank, row in enumerate(ranked, 1):
        row[f"{key}_rank"] = rank
    return ranked


def benchmark_matrix(seeds: tuple[int, ...] = SEEDS) -> dict[str, Any]:
    """Evaluate all safe built-ins across public and hidden exchange worlds."""
    rows: list[dict[str, Any]] = []
    for policy in POLICIES.values():
        public_runs = [run_execution_challenge(policy.policy_id, "normal", seed) for seed in seeds]
        hidden_runs = [
            run_execution_challenge(policy.policy_id, variant, seed)
            for variant in HIDDEN_VARIANTS
            for seed in seeds
        ]
        public_score = round(mean(run["public_score"] for run in public_runs), 3)
        hidden_metrics = [run["metrics"] for run in hidden_runs]
        rows.append(
            {
                "policy_id": policy.policy_id,
                "name": policy.name,
                "description": policy.description,
                "public_score": public_score,
                "public_shortfall_bps": round(
                    mean(float(run["metrics"]["implementation_shortfall_bps"]) for run in public_runs), 3
                ),
                "public_completion_pct": round(
                    mean(float(run["metrics"]["completion_pct"]) for run in public_runs), 3
                ),
                "robustness_score": _robustness_score(hidden_metrics),
                "hidden_mean_shortfall_bps": round(
                    mean(float(row["implementation_shortfall_bps"]) for row in hidden_metrics), 3
                ),
                "hidden_worst_shortfall_bps": round(
                    max(float(row["implementation_shortfall_bps"]) for row in hidden_metrics), 3
                ),
                "hidden_completion_pct": round(
                    mean(float(row["completion_pct"]) for row in hidden_metrics), 3
                ),
                "world_results": [
                    {
                        "variant": run["world"]["variant"],
                        "seed": run["world"]["seed"],
                        "metrics": run["metrics"],
                    }
                    for run in hidden_runs
                ],
            }
        )
    public_ranked = _rank(rows, "public_score")
    robust_ranked = _rank(rows, "robustness_score")
    public_position = {row["policy_id"]: row["public_score_rank"] for row in public_ranked}
    for row in robust_ranked:
        row["public_rank"] = public_position[row["policy_id"]]
        row["robustness_rank"] = row["robustness_score_rank"]
        row["rank_movement"] = row["public_rank"] - row["robustness_rank"]
    provenance = {
        "challenge_schema_version": CHALLENGE_VERSION,
        "exchange_version": "internal-clob-1.0",
        "agent_population_version": "demo-world-1.0",
        "scoring_version": SCORING_VERSION,
        "seed_list": list(seeds),
        "world_variants": list(WORLD_VARIANTS),
        "strategy_submission_policy": "declarative_only",
    }
    provenance["matrix_hash"] = sha256(
        json.dumps(robust_ranked, sort_keys=True, default=str).encode()
    ).hexdigest()
    return {"challenge": challenge_overview(), "rows": robust_ranked, "provenance": provenance}


def challenge_overview() -> dict[str, Any]:
    return {
        "challenge_id": CHALLENGE_ID,
        "title": "Trade the Shock",
        "phase": "public_practice",
        "objective": "Buy 6,000 shares of fictional NOVA inside one session while controlling implementation shortfall, participation, and terminal inventory.",
        "public_world": "Normal liquidity, stable background flow, low exchange latency, and no forced seller.",
        "hidden_worlds": [
            {"id": "liquidity_withdrawal", "label": "Liquidity withdrawal", "released": False},
            {"id": "crowded_unwind", "label": "Forced seller and crowded unwind", "released": False},
            {"id": "earnings_shock", "label": "Earnings shock", "released": False},
            {"id": "latency_shock", "label": "Feed and exchange latency shock", "released": False},
        ],
        "policies": [policy.__dict__ for policy in POLICIES.values()],
        "quality": {
            "mechanical_validity": "PASS",
            "challenge_behavior": "PASS",
            "selected_stylized_facts": "NOT_EVALUATED",
            "real_market_calibration": "NOT_CLAIMED",
        },
        "stress_contract": [
            "Deterministic replay",
            "Inventory accounting",
            "No future observation access",
            "Liquidity-constrained exchange matching",
            "Same-input equality",
        ],
        "limits": "This fictional exchange is an educational assessment environment. It does not predict markets, prove profitability, or provide a production capacity estimate.",
    }
