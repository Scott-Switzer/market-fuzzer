"""Exchange-backed execution challenge used by the primary Arena experience.

This module deliberately sits above the matching engine.  It does not calculate
fills or prices itself: every metric is derived from ``run_simulation``.
"""

from __future__ import annotations

import json
import os
import subprocess
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from statistics import mean, pstdev
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.simulation import SimulationResult, run_simulation
from app.world.scenarios import build_demo_world, mutate_scenario

CHALLENGE_ID = "trade-the-shock"
CHALLENGE_VERSION = "1.0"
SCORING_VERSION = "1.1"
PUBLIC_PARTICIPATION_LIMIT_PCT = 25.0
WORLD_VARIANTS = ("normal", "liquidity_withdrawal", "crowded_unwind", "earnings_shock", "latency_shock")
HIDDEN_VARIANTS = WORLD_VARIANTS[1:]
SEEDS = (41, 42)
PUBLIC_SEED = 42


@lru_cache(maxsize=1)
def _code_commit() -> str:
    """Capture deploy provenance, marking a local checkout when it is dirty."""
    configured = os.getenv("GIT_COMMIT_SHA", "").strip()
    if configured:
        return configured
    repository = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.strip()
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "unavailable"
    return f"{revision}-dirty" if dirty else revision


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
    max_participation: float | None = None
    cancel_after_ms: int | None = None
    urgency_curve: str = "uniform"
    completion_buffer_steps: int = 0
    pause_above_spread_limit: bool = False
    include_pending_in_budget: bool = True
    feed_latency_tolerance_ms: int | None = None


class ExecutionPolicySubmission(BaseModel):
    """Versioned, declarative student policy.  No submitted code is executed."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    strategy_type: Literal["twap", "pov", "adaptive_pov"]
    target_participation: float = Field(ge=0.01, le=0.20)
    max_participation: float | None = Field(default=None, ge=0.01, le=0.30)
    max_spread_bps: float = Field(ge=1, le=50)
    urgency_curve: Literal["uniform", "front_loaded", "back_loaded", "adaptive"]
    feed_latency_tolerance_ms: int = Field(ge=0, le=10_000)
    order_entry_latency_ms: int = Field(default=5, ge=0, le=10_000)
    cancel_after_ms: int | None = Field(default=None, ge=10, le=10_000)
    completion_buffer_steps: int = Field(ge=0, le=20)
    pause_during_halt: bool
    pause_above_spread_limit: bool
    include_pending_in_budget: bool
    rationale: str = Field(min_length=50, max_length=2_000)

    @model_validator(mode="after")
    def _valid_combination(self) -> ExecutionPolicySubmission:
        if self.max_participation is not None and self.max_participation < self.target_participation:
            raise ValueError("max_participation must be at least target_participation")
        if self.strategy_type == "twap" and self.urgency_curve == "adaptive":
            raise ValueError("TWAP does not support an adaptive urgency curve")
        return self


def policy_from_submission(submission: ExecutionPolicySubmission, submission_id: str) -> Policy:
    """Map only supported declarative controls onto the deterministic engine."""
    strategy = "twap" if submission.strategy_type == "twap" else "pov"
    return Policy(
        policy_id=submission_id,
        name="Student policy",
        description="Student-authored declarative execution policy.",
        strategy=strategy,
        participation_rate=submission.target_participation,
        latency_ms=submission.order_entry_latency_ms,
        max_spread_bps=submission.max_spread_bps,
        pause_during_halt=submission.pause_during_halt,
        max_participation=submission.max_participation,
        cancel_after_ms=submission.cancel_after_ms,
        urgency_curve=submission.urgency_curve,
        completion_buffer_steps=submission.completion_buffer_steps,
        pause_above_spread_limit=submission.pause_above_spread_limit,
        include_pending_in_budget=submission.include_pending_in_budget,
        feed_latency_tolerance_ms=submission.feed_latency_tolerance_ms,
    )


def policy_to_submission(policy: Policy, *, rationale: str | None = None) -> ExecutionPolicySubmission:
    """Serialize a benchmark through the same public contract students use."""
    strategy_type = (
        "twap"
        if policy.strategy == "twap"
        else "adaptive_pov"
        if policy.urgency_curve == "adaptive"
        else "pov"
    )
    return ExecutionPolicySubmission(
        strategy_type=strategy_type,
        target_participation=policy.participation_rate,
        max_participation=policy.max_participation,
        max_spread_bps=policy.max_spread_bps,
        urgency_curve=policy.urgency_curve,
        feed_latency_tolerance_ms=(
            policy.feed_latency_tolerance_ms if policy.feed_latency_tolerance_ms is not None else 10_000
        ),
        order_entry_latency_ms=policy.latency_ms,
        cancel_after_ms=policy.cancel_after_ms,
        completion_buffer_steps=policy.completion_buffer_steps,
        pause_during_halt=policy.pause_during_halt,
        pause_above_spread_limit=policy.pause_above_spread_limit,
        include_pending_in_budget=policy.include_pending_in_budget,
        rationale=rationale
        or "Public benchmark policy serialized through the student contract for parity testing.",
    )


POLICIES: dict[str, Policy] = {
    "twap": Policy(
        "twap", "TWAP benchmark", "Evenly schedules the order across the horizon.", "twap", 0.08, 5
    ),
    "aggressive_pov": Policy(
        "aggressive_pov",
        "Aggressive POV",
        "Completes quickly in public practice, but accepts more flow under adverse conditions.",
        "pov",
        0.12,
        4,
    ),
    "guarded_pov": Policy(
        "guarded_pov",
        "Guarded adaptive POV",
        "Uses a lower participation cap and preserves room for adverse conditions.",
        "pov",
        0.08,
        0,
        max_spread_bps=20.0,
        max_participation=0.20,
        cancel_after_ms=250,
        urgency_curve="adaptive",
        completion_buffer_steps=20,
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


def _world_for(policy: Policy, variant: str, seed: int) -> tuple[Any, dict[str, Any], str]:
    base = build_demo_world(seed)
    base, changes = mutate_scenario(base, "normal" if variant == "latency_shock" else variant)
    data = deepcopy(base.model_dump(mode="python"))
    data["world_id"] = f"{CHALLENGE_ID}-{variant}-{seed}"
    data["ground_truth_labels"].update(
        {
            "challenge_id": CHALLENGE_ID,
            "challenge_version": CHALLENGE_VERSION,
            "world_variant": variant,
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
    environment_hash = sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    data["world_id"] = f"{CHALLENGE_ID}-{variant}-{policy.policy_id}-{seed}"
    data["experiment"]["strategy"] = policy.strategy
    data["experiment"]["participation_rate"] = policy.participation_rate
    data["experiment"]["latency_ms"] = policy.latency_ms
    data["experiment"]["parent_order"]["quantity"] = policy.parent_quantity
    data["experiment"]["counterfactual_mutations"] = list(WORLD_VARIANTS)
    data["ground_truth_labels"]["policy_id"] = policy.policy_id
    # The strategy's entry latency is independent from the shared exchange profile.
    for population in data["agents"]["populations"]:
        if population["type"] == "execution":
            population["latency_ms"] = policy.latency_ms
            population["parameters"].update(
                {
                    "max_participation": policy.max_participation or policy.participation_rate,
                    "enforce_max_participation": policy.max_participation is not None,
                    "max_spread_bps": policy.max_spread_bps,
                    "urgency_curve": policy.urgency_curve,
                    "cancel_after_ms": policy.cancel_after_ms or 10_000,
                    "completion_buffer_steps": policy.completion_buffer_steps,
                    "pause_during_halt": policy.pause_during_halt,
                    "pause_above_spread_limit": policy.pause_above_spread_limit,
                    "include_pending_in_budget": policy.include_pending_in_budget,
                    "feed_latency_tolerance_ms": (
                        policy.feed_latency_tolerance_ms
                        if policy.feed_latency_tolerance_ms is not None
                        else 10_000
                    ),
                }
            )
    return type(base).model_validate(data), changes, environment_hash


def _execution_metrics(result: SimulationResult, policy: Policy) -> dict[str, Any]:
    summary = result.summary
    trades = [
        row
        for row in result.trades
        if row["buyer_id"] == "execution-01" or row["seller_id"] == "execution-01"
    ]
    target = policy.parent_quantity
    executed = int(summary["filled_quantity"])
    market_volume = max(1, int(summary["target_market_volume"]))
    participation_by_step = [
        float(row["participation"]) for row in result.strategy_steps if int(row["market_volume"]) > 0
    ]
    peak_participation = max(participation_by_step, default=0.0)
    participation_weighted_duration = (
        sum(participation_by_step) / peak_participation if peak_participation > 0 else 0.0
    )
    limit = policy.max_participation or policy.participation_rate
    violation_steps = [
        int(row["step"]) for row in result.strategy_steps if float(row["participation"]) > limit + 1e-12
    ]
    inventory_path = [int(row["remaining_parent_quantity"]) for row in result.strategy_steps]
    strategy_orders = [row for row in result.orders if row["agent_id"] == "execution-01"]
    strategy_cancels = [row for row in result.cancels if row["agent_id"] == "execution-01"]
    resting_durations = [
        int(row["resting_duration_steps"])
        for row in strategy_orders
        if row.get("resting_duration_steps") is not None
    ]
    cancelled_quantity = sum(int(row["cancelled_quantity"]) for row in strategy_cancels)
    orders_during_halt = sum(
        row.get("status") == "rejected" and "halted" in str(row.get("rejection_reason", ""))
        for row in strategy_orders
    )
    accounting_ties = all(
        bool(row["child_order_accounting_ties"])
        and bool(row["parent_inventory_accounting_ties"])
        and bool(row["strategy_inventory_accounting_ties"])
        for row in result.strategy_steps
    )
    return {
        "arrival_price_ticks": round(float(summary["arrival_price_ticks"]), 3),
        "market_vwap_ticks": round(float(summary["market_vwap_ticks"]), 3),
        "average_execution_price_ticks": round(float(summary["average_execution_price_ticks"]), 3),
        "implementation_shortfall_bps": round(float(summary["implementation_shortfall_bps"]), 3),
        "temporary_impact_bps": round(float(summary["temporary_impact_bps"]), 3),
        "persistent_impact_bps": round(float(summary["persistent_impact_bps"]), 3),
        "completion_pct": round(100 * float(summary["fill_rate"]), 2),
        "remaining_inventory": int(summary["remaining_inventory"]),
        "active_child_quantity": int(summary["strategy_active_quantity"]),
        "terminal_inventory_penalty": round(100 * (target - executed) / target, 3),
        "average_participation_pct": round(100 * executed / market_volume, 3),
        "max_participation_pct": round(100 * peak_participation, 3),
        "participation_limit_pct": round(100 * limit, 3),
        # Equivalent number of peak-participation steps: sum of measured
        # per-step participation divided by the measured peak participation.
        "participation_weighted_duration_steps": round(participation_weighted_duration, 5),
        "participation_limit_violations": len(violation_steps),
        "first_participation_violation_step": violation_steps[0] if violation_steps else None,
        "time_weighted_remaining_parent_quantity": (
            round(mean(inventory_path), 3) if inventory_path else 0.0
        ),
        "fill_ratio": round(executed / target, 5),
        "adverse_selection_bps": round(float(summary["adverse_selection_bps"]), 3),
        "spread_paid_bps": round(float(summary["spread_paid_bps"]), 3),
        "market_disruption": round(float(summary["market_disruption"]), 3),
        "strategy_trade_count": len(trades),
        "orders_submitted": len(strategy_orders),
        "orders_cancelled": len(strategy_cancels),
        "orders_rejected": sum(row.get("status") == "rejected" for row in strategy_orders),
        "orders_active_at_horizon": sum(int(row.get("active_quantity", 0)) > 0 for row in strategy_orders),
        "orders_active_after_parent_completion": sum(
            int(row["active_child_order_count"])
            for row in result.strategy_steps
            if int(row["remaining_parent_quantity"]) == 0
        ),
        "orders_during_halt": orders_during_halt,
        "peak_concurrent_active_orders": max(
            (int(row["active_child_order_count"]) for row in result.strategy_steps), default=0
        ),
        "mean_resting_duration_steps": round(mean(resting_durations), 3) if resting_durations else None,
        "max_resting_duration_steps": max(resting_durations, default=None),
        "cancel_to_fill_quantity_ratio": round(cancelled_quantity / executed, 5) if executed else None,
        "order_hygiene_scored": False,
        "inventory_accounting_ties": accounting_ties,
    }


def _number(metrics: dict[str, Any], key: str) -> float:
    value = metrics[key]
    if value is None:
        raise ValueError(f"required execution metric {key!r} was absent")
    return float(value)


def _correlation(left: list[float], right: list[float]) -> float:
    """Return a bounded population correlation, or zero for a constant series."""
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    left_mean = mean(left)
    right_mean = mean(right)
    left_variance = sum((value - left_mean) ** 2 for value in left)
    right_variance = sum((value - right_mean) ** 2 for value in right)
    denominator = (left_variance * right_variance) ** 0.5
    if denominator == 0:
        return 0.0
    covariance = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right, strict=True)
    )
    return max(-1.0, min(1.0, covariance / denominator))


def _distribution(values: list[float], digits: int = 3) -> dict[str, float]:
    return {
        "minimum": round(min(values), digits),
        "mean": round(mean(values), digits),
        "maximum": round(max(values), digits),
    }


def _selected_synthetic_diagnostics(result: SimulationResult) -> dict[str, Any]:
    """Report a small transparent diagnostic set without claiming calibration."""
    states = [frame["asset_states"]["NOVA"] for frame in result.timeline]
    mids = [float(state["mid_ticks"]) for state in states]
    spreads = [float(state["spread_ticks"] or 0) for state in states]
    depths = [float(state["bid_depth"] + state["ask_depth"]) for state in states]
    volumes = [float(row["market_volume"]) for row in result.strategy_steps]
    returns_bps = [0.0]
    returns_bps.extend(
        10_000 * (current - previous) / previous
        for previous, current in zip(mids[:-1], mids[1:], strict=True)
        if previous
    )

    order_sides = {str(order["order_id"]): str(order["side"]) for order in result.orders}
    signed_flow_by_step = [0.0 for _ in result.timeline]
    for trade in result.trades:
        if trade["symbol"] != "NOVA":
            continue
        side = order_sides.get(str(trade["taker_order_id"]))
        if side == "buy":
            signed_flow_by_step[int(trade["step"])] += float(trade["quantity"])
        elif side == "sell":
            signed_flow_by_step[int(trade["step"])] -= float(trade["quantity"])

    event_types = sorted({str(event["type"]) for event in result.events if event.get("type")})
    forced_sell_quantity = sum(
        int(trade["quantity"])
        for trade in result.trades
        if str(trade["seller_id"]).startswith(("forced_liquidator", "intervention-forced-seller"))
    )
    accounting_all_steps = all(
        bool(frame["accounting"]["cash_conservation"])
        and all(bool(value) for value in frame["accounting"]["inventory_conservation"].values())
        and bool(frame["accounting"]["strategy_inventory_ties"])
        and bool(frame["accounting"]["parent_capacity_ties"])
        for frame in result.timeline
    )
    return {
        "scope": "selected_synthetic_market_diagnostics_not_real_market_calibration",
        "spread_distribution_ticks": _distribution(spreads),
        "displayed_depth_distribution_shares": _distribution(depths),
        "return_volatility_bps": round(pstdev(returns_bps), 3),
        "volume_clustering_lag1": round(_correlation(volumes[:-1], volumes[1:]), 5),
        "depth_spread_correlation": round(_correlation(depths, spreads), 5),
        "price_response_to_signed_flow_correlation": round(_correlation(signed_flow_by_step, returns_bps), 5),
        "forced_sell_quantity": forced_sell_quantity,
        "scheduled_event_types": event_types,
        "latency_ms": {
            key: int(result.latency_profile[key])
            for key in ("feed_ms", "decision_ms", "order_entry_ms", "cancel_ms")
        },
        "accounting_all_steps": accounting_all_steps,
    }


def _replay_payload(result: SimulationResult, policy: Policy) -> dict[str, Any]:
    strategy_id = "execution-01"
    strategy_orders = [row for row in result.orders if row["agent_id"] == strategy_id]
    strategy_cancels = [row for row in result.cancels if row["agent_id"] == strategy_id]
    strategy_trades: list[dict[str, Any]] = []
    for trade in result.trades:
        if trade["maker_id"] == strategy_id:
            order_id = trade["maker_order_id"]
            queue_model = "price_time_priority_simplified"
            queue_ahead = trade["maker_queue_ahead_at_entry"]
            traded_before = trade["quantity_traded_at_level_before_fill"]
            partial_fill_sequence = trade["maker_partial_fill_sequence"]
        elif trade["taker_id"] == strategy_id:
            order_id = trade["taker_order_id"]
            queue_model = "not_applicable_taker_order"
            queue_ahead = None
            traded_before = None
            partial_fill_sequence = trade["taker_partial_fill_sequence"]
        else:
            continue
        strategy_trades.append(
            {
                **trade,
                "timestamp_ms": trade["fill_time_ms"],
                "order_id": order_id,
                "side": "buy" if trade["buyer_id"] == strategy_id else "sell",
                "price": trade["price_ticks"],
                "maker_id": trade["maker_id"],
                "taker_id": trade["taker_id"],
                "strategy_id": strategy_id,
                "queue_model": queue_model,
                "displayed_quantity_ahead_at_entry": queue_ahead,
                "quantity_traded_at_level_before_fill": traded_before,
                "partial_fill_sequence": partial_fill_sequence,
            }
        )

    orders_by_step: dict[int, list[dict[str, Any]]] = {}
    for order in strategy_orders:
        orders_by_step.setdefault(int(order["submitted_step"]), []).append(order)
    cancels_by_step: dict[int, list[dict[str, Any]]] = {}
    for cancel in strategy_cancels:
        cancels_by_step.setdefault(int(cancel["effective_step"]), []).append(cancel)
    trades_by_step: dict[int, list[dict[str, Any]]] = {}
    for trade in strategy_trades:
        trades_by_step.setdefault(int(trade["step"]), []).append(trade)

    evidence_rows: list[dict[str, Any]] = []
    for frame, activity in zip(result.timeline, result.strategy_steps, strict=True):
        step = int(frame["step"])
        state = frame["asset_states"]["NOVA"]
        events = [
            str(event.get("type", "event"))
            for event in frame["events"]
            if event.get("asset") in (None, "NOVA")
        ]
        submitted_orders = orders_by_step.get(step, [])
        effective_cancels = cancels_by_step.get(step, [])
        fills = trades_by_step.get(step, [])
        actions: list[str] = []
        if submitted_orders:
            actions.append("submit")
        if effective_cancels:
            actions.append("cancel")
        if fills:
            actions.append("fill")
        evidence_rows.append(
            {
                "step": step,
                "market_event": ", ".join(events) if events else None,
                "best_bid_ticks": state["best_bid_ticks"],
                "best_ask_ticks": state["best_ask_ticks"],
                "spread_ticks": state["spread_ticks"],
                "displayed_depth": int(state["bid_depth"]) + int(state["ask_depth"]),
                "observed_volume": activity["observed_volume"],
                "order_action": actions,
                "order_ids": [row["order_id"] for row in submitted_orders],
                "order_quantity": activity["strategy_submitted_quantity"],
                "fill_quantity": activity["strategy_filled_quantity"],
                "remaining_inventory": activity["remaining_parent_quantity"],
                "active_child_quantity": activity["strategy_active_quantity"],
                "participation_pct": round(100 * float(activity["participation"]), 3),
                "participation_limit_pct": round(
                    100 * float(policy.max_participation or policy.participation_rate), 3
                ),
                "shortfall_contribution_bps": round(float(activity["shortfall_contribution_bps"]), 5),
            }
        )
    return {
        "timeline": result.timeline,
        "events": result.events,
        "trades": result.trades,
        "orders": strategy_orders,
        "cancels": strategy_cancels,
        "strategy_trades": strategy_trades,
        "strategy_activity": result.strategy_steps,
        "strategy_observations": result.strategy_observations,
        "latency_profile": result.latency_profile,
        "evidence_rows": evidence_rows,
        "queue_evidence": {
            "model": "price_time_priority_simplified",
            "scope": "Exact displayed quantity ahead and traded-at-price counters are recorded for resting limit orders; market takers have no queue position.",
        },
    }


def _public_score(metrics: dict[str, Any]) -> float:
    """Practice rubric: completion, cost, terminal inventory, and a visible 25% cap."""
    participation_excess = max(
        0.0, _number(metrics, "max_participation_pct") - PUBLIC_PARTICIPATION_LIMIT_PCT
    )
    return round(
        _number(metrics, "completion_pct") * 10
        - max(0.0, _number(metrics, "implementation_shortfall_bps"))
        - 2 * _number(metrics, "terminal_inventory_penalty")
        - 2 * participation_excess,
        3,
    )


def _robustness_decomposition(metrics_by_world: list[dict[str, Any]]) -> dict[str, float | None]:
    shortfalls = [max(0.0, _number(row, "implementation_shortfall_bps")) for row in metrics_by_world]
    completions = [_number(row, "completion_pct") for row in metrics_by_world]
    impacts = [max(0.0, _number(row, "temporary_impact_bps")) for row in metrics_by_world]
    inventory_penalties = [_number(row, "terminal_inventory_penalty") for row in metrics_by_world]
    max_participations = [_number(row, "max_participation_pct") for row in metrics_by_world]
    participation_violations = [_number(row, "participation_limit_violations") for row in metrics_by_world]
    inventory_paths = [_number(row, "time_weighted_remaining_parent_quantity") for row in metrics_by_world]
    components = {
        "shortfall_mean": 30 * max(0.0, 1 - mean(shortfalls) / 250),
        "completion": 15
        * (0.75 * mean(completions) / 100 + 0.25 * max(0.0, 1 - mean(inventory_penalties) / 100)),
        "impact": 10 * max(0.0, 1 - mean(impacts) / 450),
        "shortfall_worst": 15 * max(0.0, 1 - max(shortfalls) / 250),
        "participation_discipline": 15
        * (
            0.7 * max(0.0, 1 - mean(max_participations) / 50)
            + 0.3 * max(0.0, 1 - mean(participation_violations) / 120)
        ),
        "inventory_risk": 10 * max(0.0, 1 - mean(inventory_paths) / 6_000),
        "stability": 5 * max(0.0, 1 - pstdev(shortfalls) / 100),
        # The engine reports actual order-hygiene evidence, but market-order-only
        # policies do not produce a comparable resting-order quality signal.
        "order_hygiene": None,
    }
    rounded = {key: round(value, 3) if value is not None else None for key, value in components.items()}
    rounded["total"] = round(sum(value for value in rounded.values() if value is not None), 3)
    return rounded


def _run_policy(policy: Policy, world_variant: str, seed: int) -> dict[str, Any]:
    if world_variant not in WORLD_VARIANTS:
        raise ValueError(f"unknown world variant {world_variant!r}")
    spec, changes, environment_hash = _world_for(policy, world_variant, seed)
    result = run_simulation(spec)
    metrics = _execution_metrics(result, policy)
    diagnostics = _selected_synthetic_diagnostics(result)
    return {
        "challenge_id": CHALLENGE_ID,
        "phase": "public_practice" if world_variant == "normal" else "hidden_evaluation",
        "policy": policy.__dict__,
        "world": {
            "variant": world_variant,
            "seed": seed,
            "changes": changes,
            "specification_hash": result.spec_hash,
            "policy_specification_hash": result.spec_hash,
            "environment_hash": environment_hash,
        },
        "metrics": metrics,
        "public_score": _public_score(metrics),
        "replay": _replay_payload(result, policy),
        "evidence": {
            "mechanical_validity": "PASS",
            "challenge_behavior": "SINGLE_WORLD_ONLY",
            "selected_synthetic_diagnostics": diagnostics,
            "real_market_calibration": "NOT_CLAIMED",
            "result_hash": result.result_hash,
        },
    }


def run_execution_challenge(policy_id: str, world_variant: str = "normal", seed: int = 42) -> dict[str, Any]:
    if policy_id not in POLICIES:
        raise ValueError(f"unknown declarative policy {policy_id!r}")
    return _run_policy(POLICIES[policy_id], world_variant, seed)


def run_policy_submission(
    submission: ExecutionPolicySubmission, submission_id: str, seed: int = 42
) -> dict[str, Any]:
    """Run a student submission in the public world only.

    Hidden-world selection deliberately remains private to the evaluation service.
    """
    policy = policy_from_submission(submission, submission_id)
    run = _run_policy(policy, "normal", seed)
    run["policy"] = submission.model_dump(mode="json")
    return run


def _bounded_trace_evidence(run: dict[str, Any]) -> dict[str, Any]:
    replay = run["replay"]
    order_ids = [str(row["order_id"]) for row in replay["orders"]]
    trade_ids = [str(row["trade_id"]) for row in replay["strategy_trades"]]
    event_ids = [str(row["event_id"]) for row in replay["events"] if row.get("event_id")]
    replay_steps = [
        int(row["step"])
        for row in replay["strategy_activity"]
        if row["strategy_submitted_quantity"]
        or row["strategy_cancelled_quantity"]
        or row["strategy_filled_quantity"]
    ]
    limit = 256
    return {
        "strategy_order_ids": order_ids[:limit],
        "strategy_trade_ids": trade_ids[:limit],
        "event_ids": event_ids[:limit],
        "replay_step_ids": replay_steps[:limit],
        "latency_profile": replay["latency_profile"],
        "summary": {
            "filled_quantity": round(6_000 * float(run["metrics"]["fill_ratio"])),
            "completion_pct": run["metrics"]["completion_pct"],
            "remaining_inventory": run["metrics"]["remaining_inventory"],
            "max_participation_pct": run["metrics"]["max_participation_pct"],
            "inventory_accounting_ties": run["metrics"]["inventory_accounting_ties"],
        },
        "trace_limit_per_field": limit,
        "trace_truncated": any(len(rows) > limit for rows in (order_ids, trade_ids, event_ids, replay_steps)),
    }


def _world_result(run: dict[str, Any], *, include_trace: bool) -> dict[str, Any]:
    result = {
        "world_id": f"{CHALLENGE_ID}-{run['world']['variant']}-{run['world']['seed']}",
        "variant": run["world"]["variant"],
        "seed": run["world"]["seed"],
        "world_hash": run["world"]["environment_hash"],
        "environment_hash": run["world"]["environment_hash"],
        "policy_specification_hash": run["world"]["policy_specification_hash"],
        "result_hash": run["evidence"]["result_hash"],
        "metrics": run["metrics"],
        "selected_synthetic_diagnostics": run["evidence"]["selected_synthetic_diagnostics"],
    }
    if include_trace:
        result["trace_evidence"] = _bounded_trace_evidence(run)
    return result


def evaluate_submission_matrix(
    submission: ExecutionPolicySubmission,
    submission_id: str,
    *,
    variants: tuple[str, ...] = HIDDEN_VARIANTS,
    seeds: tuple[int, ...] = SEEDS,
) -> dict[str, Any]:
    """Evaluate a validated custom policy through the same protected engine adapter."""
    if (
        not variants
        or len(set(variants)) != len(variants)
        or any(variant not in HIDDEN_VARIANTS for variant in variants)
    ):
        raise ValueError("submission evaluation variants must be server-selected hidden variants")
    if not seeds:
        raise ValueError("submission evaluation requires at least one deterministic seed")
    policy = policy_from_submission(submission, submission_id)
    runs = [_run_policy(policy, variant, seed) for variant in variants for seed in seeds]
    metrics = [run["metrics"] for run in runs]
    decomposition = _robustness_decomposition(metrics)
    world_results = [_world_result(run, include_trace=True) for run in runs]
    record: dict[str, Any] = {
        "challenge_id": CHALLENGE_ID,
        "submission_id": submission_id,
        "policy_version": submission.schema_version,
        "exchange_version": "internal-clob-1.1",
        "agent_version": "demo-world-1.0",
        "scoring_version": SCORING_VERSION,
        "public_seed": PUBLIC_SEED,
        "seed_list": list(seeds),
        "protected_variants": list(variants),
        "code_commit": _code_commit(),
        "world_results": world_results,
        "aggregate_metrics": {
            "robustness_score": decomposition["total"],
            "score_decomposition": decomposition,
            "hidden_mean_shortfall_bps": round(
                mean(float(row["implementation_shortfall_bps"]) for row in metrics), 3
            ),
            "hidden_worst_shortfall_bps": round(
                max(float(row["implementation_shortfall_bps"]) for row in metrics), 3
            ),
            "hidden_completion_pct": round(mean(float(row["completion_pct"]) for row in metrics), 3),
        },
    }
    record["matrix_hash"] = sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return record


def _rank(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    ranked = sorted(rows, key=lambda row: row[key], reverse=True)
    for rank, row in enumerate(ranked, 1):
        row[f"{key}_rank"] = rank
    return ranked


RELEASED_INTENT_IDS = {
    "liquidity_withdrawal": "thin_liquidity",
    "latency_shock": "message_latency",
    "crowded_unwind": "directional_crowding",
    "earnings_shock": "scheduled_event",
}


def _released_intent_aggregates(hidden_runs: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Project protected runs into coarse, release-safe educational aggregates.

    No seed rows, hashes, order streams, or internal world identifiers are
    exposed by this projection. The stable intent IDs describe the educational
    mechanism and are deliberately distinct from private implementation names.
    """
    aggregates: dict[str, dict[str, float]] = {}
    for variant, intent_id in RELEASED_INTENT_IDS.items():
        selected = [run for run in hidden_runs if run["world"]["variant"] == variant]
        if not selected:
            continue
        diagnostics = [run["evidence"]["selected_synthetic_diagnostics"] for run in selected]
        event_activations = [float(bool(diagnostic["scheduled_event_types"])) for diagnostic in diagnostics]
        aggregates[intent_id] = {
            "implementation_shortfall_bps": round(
                mean(float(run["metrics"]["implementation_shortfall_bps"]) for run in selected), 3
            ),
            "completion_pct": round(mean(float(run["metrics"]["completion_pct"]) for run in selected), 3),
            "maximum_participation_pct": round(
                mean(float(run["metrics"]["max_participation_pct"]) for run in selected), 3
            ),
            "mean_displayed_depth_shares": round(
                mean(
                    float(diagnostic["displayed_depth_distribution_shares"]["mean"])
                    for diagnostic in diagnostics
                ),
                3,
            ),
            "order_entry_latency_ms": round(
                mean(float(diagnostic["latency_ms"]["order_entry_ms"]) for diagnostic in diagnostics),
                3,
            ),
            "forced_sell_quantity": round(
                mean(float(diagnostic["forced_sell_quantity"]) for diagnostic in diagnostics), 3
            ),
            "event_activation_rate": round(mean(event_activations), 3),
        }
    return aggregates


def _matrix_row(policy: Policy, seeds: tuple[int, ...], variants: tuple[str, ...]) -> dict[str, Any]:
    public_runs = [_run_policy(policy, "normal", PUBLIC_SEED)]
    hidden_runs = [_run_policy(policy, variant, seed) for variant in variants for seed in seeds]
    hidden_metrics = [run["metrics"] for run in hidden_runs]
    score_decomposition = _robustness_decomposition(hidden_metrics)
    return {
        "policy_id": policy.policy_id,
        "policy_version": "builtin-1.0",
        "name": policy.name,
        "description": policy.description,
        "public_score": round(mean(run["public_score"] for run in public_runs), 3),
        "public_shortfall_bps": round(
            mean(float(run["metrics"]["implementation_shortfall_bps"]) for run in public_runs), 3
        ),
        "public_completion_pct": round(
            mean(float(run["metrics"]["completion_pct"]) for run in public_runs), 3
        ),
        "robustness_score": score_decomposition["total"],
        "score_decomposition": score_decomposition,
        "hidden_mean_shortfall_bps": round(
            mean(float(row["implementation_shortfall_bps"]) for row in hidden_metrics), 3
        ),
        "hidden_worst_shortfall_bps": round(
            max(float(row["implementation_shortfall_bps"]) for row in hidden_metrics), 3
        ),
        "hidden_completion_pct": round(mean(float(row["completion_pct"]) for row in hidden_metrics), 3),
        "released_intent_aggregates": _released_intent_aggregates(hidden_runs),
        "public_world_results": [_world_result(run, include_trace=False) for run in public_runs],
        "world_results": [_world_result(run, include_trace=True) for run in hidden_runs],
    }


def _rank_matrix_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    public_ranked = _rank(rows, "public_score")
    robust_ranked = _rank(rows, "robustness_score")
    public_position = {row["policy_id"]: row["public_score_rank"] for row in public_ranked}
    for row in robust_ranked:
        row["public_rank"] = public_position[row["policy_id"]]
        row["robustness_rank"] = row["robustness_score_rank"]
        row["rank_movement"] = row["public_rank"] - row["robustness_rank"]
    return robust_ranked


@lru_cache(maxsize=1)
def _determinism_probe_result_hash() -> str:
    return str(_run_policy(POLICIES["aggressive_pov"], "normal", PUBLIC_SEED)["evidence"]["result_hash"])


def _challenge_quality_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate the declared protected-world behavior using measured run evidence."""
    reference = next((row for row in rows if row["policy_id"] == "aggressive_pov"), None)
    if reference is None:
        return {
            "challenge_behavior": {"status": "INCOMPLETE", "checks": []},
            "selected_synthetic_diagnostics": {"status": "NOT_AVAILABLE"},
        }

    public = next(
        (result for result in reference.get("public_world_results", []) if result.get("seed") == PUBLIC_SEED),
        None,
    )
    protected = {
        result["variant"]: result
        for result in reference.get("world_results", [])
        if result.get("seed") == PUBLIC_SEED
    }
    if public is None:
        return {
            "challenge_behavior": {"status": "INCOMPLETE", "checks": []},
            "selected_synthetic_diagnostics": {"status": "NOT_AVAILABLE"},
        }

    public_diagnostics = public["selected_synthetic_diagnostics"]
    thin = protected.get("liquidity_withdrawal")
    delayed = protected.get("latency_shock")
    crowded = protected.get("crowded_unwind")
    scheduled = protected.get("earnings_shock")

    def diagnostic(result: dict[str, Any] | None) -> dict[str, Any]:
        return result.get("selected_synthetic_diagnostics", {}) if result is not None else {}

    thin_diagnostics = diagnostic(thin)
    delayed_diagnostics = diagnostic(delayed)
    crowded_diagnostics = diagnostic(crowded)
    scheduled_diagnostics = diagnostic(scheduled)
    all_results = [
        result
        for row in rows
        for result in [*row.get("public_world_results", []), *row.get("world_results", [])]
    ]
    checks = [
        {
            "id": "liquidity_reduces_displayed_depth",
            "passed": bool(thin_diagnostics)
            and float(thin_diagnostics["displayed_depth_distribution_shares"]["mean"])
            < float(public_diagnostics["displayed_depth_distribution_shares"]["mean"]),
            "public_mean_depth": public_diagnostics["displayed_depth_distribution_shares"]["mean"],
            "protected_mean_depth": thin_diagnostics.get("displayed_depth_distribution_shares", {}).get(
                "mean"
            ),
        },
        {
            "id": "latency_increases_order_entry_delay",
            "passed": bool(delayed_diagnostics)
            and float(delayed_diagnostics["latency_ms"]["order_entry_ms"])
            > float(public_diagnostics["latency_ms"]["order_entry_ms"]),
            "public_order_entry_ms": public_diagnostics["latency_ms"]["order_entry_ms"],
            "protected_order_entry_ms": delayed_diagnostics.get("latency_ms", {}).get("order_entry_ms"),
        },
        {
            "id": "crowding_increases_directional_sell_flow",
            "passed": bool(crowded_diagnostics)
            and float(crowded_diagnostics["forced_sell_quantity"])
            > float(public_diagnostics["forced_sell_quantity"]),
            "public_forced_sell_quantity": public_diagnostics["forced_sell_quantity"],
            "protected_forced_sell_quantity": crowded_diagnostics.get("forced_sell_quantity"),
        },
        {
            "id": "scheduled_event_activates",
            "passed": "earnings" in scheduled_diagnostics.get("scheduled_event_types", []),
            "observed_event_types": scheduled_diagnostics.get("scheduled_event_types", []),
        },
        {
            "id": "all_worlds_preserve_accounting",
            "passed": bool(all_results)
            and all(
                bool(result["selected_synthetic_diagnostics"]["accounting_all_steps"])
                and bool(result["metrics"]["inventory_accounting_ties"])
                for result in all_results
            ),
            "checked_result_count": len(all_results),
        },
        {
            "id": "same_input_reproduces_identical_result_hash",
            "passed": str(public["result_hash"]) == _determinism_probe_result_hash(),
            "recorded_result_hash": public["result_hash"],
            "replayed_result_hash": _determinism_probe_result_hash(),
        },
    ]
    status = "PASS" if all(bool(check["passed"]) for check in checks) else "FAIL"
    return {
        "challenge_behavior": {
            "status": status,
            "definition": "PASS requires every listed measured behavior and accounting check to pass.",
            "checks": checks,
        },
        "selected_synthetic_diagnostics": {
            "status": "REPORTED_NOT_CALIBRATED",
            "metrics": [
                "spread_distribution_ticks",
                "displayed_depth_distribution_shares",
                "return_volatility_bps",
                "volume_clustering_lag1",
                "depth_spread_correlation",
                "price_response_to_signed_flow_correlation",
            ],
            "claim_boundary": "These synthetic diagnostics do not establish real-market calibration.",
        },
    }


@lru_cache(maxsize=16)
def _builtin_benchmark_matrix(seeds: tuple[int, ...], variants: tuple[str, ...]) -> dict[str, Any]:
    """Cache only immutable built-in rows; callers always receive a deep copy."""
    rows: list[dict[str, Any]] = []
    for policy in POLICIES.values():
        rows.append(_matrix_row(policy, seeds, variants))
    robust_ranked = _rank_matrix_rows(rows)
    provenance = {
        "challenge_schema_version": CHALLENGE_VERSION,
        "exchange_version": "internal-clob-1.1",
        "agent_population_version": "demo-world-1.0",
        "scoring_version": SCORING_VERSION,
        "public_seed": PUBLIC_SEED,
        "seed_list": list(seeds),
        "world_variants": ["normal", *variants],
        "protected_variants": list(variants),
        "hidden_world_count": len(variants),
        "code_commit": _code_commit(),
        "strategy_submission_policy": "declarative_only",
        "order_hygiene_scoring": "omitted_market_order_only_not_comparable",
        "quality": _challenge_quality_report(robust_ranked),
    }
    provenance["matrix_hash"] = sha256(
        json.dumps({"rows": robust_ranked, "provenance": provenance}, sort_keys=True, default=str).encode()
    ).hexdigest()
    return {"challenge": challenge_overview(), "rows": robust_ranked, "provenance": provenance}


def benchmark_matrix(
    seeds: tuple[int, ...] = SEEDS,
    variants: tuple[str, ...] = HIDDEN_VARIANTS,
    student_submissions: dict[str, ExecutionPolicySubmission] | None = None,
) -> dict[str, Any]:
    """Return cached built-ins and evaluate only newly supplied custom policies."""
    variants = tuple(variants)
    if (
        not variants
        or len(set(variants)) != len(variants)
        or any(variant not in HIDDEN_VARIANTS for variant in variants)
    ):
        raise ValueError("benchmark variants must be a unique non-empty subset of hidden variants")
    matrix = deepcopy(_builtin_benchmark_matrix(tuple(seeds), variants))
    if not student_submissions:
        return matrix
    rows = matrix["rows"]
    for submission_id, submission in student_submissions.items():
        row = _matrix_row(policy_from_submission(submission, submission_id), tuple(seeds), variants)
        row["submission_id"] = submission_id
        row["policy_version"] = submission.schema_version
        rows.append(row)
    matrix["rows"] = _rank_matrix_rows(rows)
    matrix["provenance"]["student_submission_ids"] = sorted(student_submissions)
    matrix["provenance"]["quality"] = _challenge_quality_report(matrix["rows"])
    matrix["provenance"].pop("matrix_hash", None)
    matrix["provenance"]["matrix_hash"] = sha256(
        json.dumps(
            {"rows": matrix["rows"], "provenance": matrix["provenance"]},
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    return matrix


def _public_row(policy: Policy) -> dict[str, Any]:
    runs = [_run_policy(policy, "normal", PUBLIC_SEED)]
    return {
        "policy_id": policy.policy_id,
        "policy_version": "builtin-1.0",
        "name": policy.name,
        "description": policy.description,
        "public_score": round(mean(run["public_score"] for run in runs), 3),
        "public_shortfall_bps": round(
            mean(float(run["metrics"]["implementation_shortfall_bps"]) for run in runs), 3
        ),
        "public_completion_pct": round(mean(float(run["metrics"]["completion_pct"]) for run in runs), 3),
        "world_results": [_world_result(run, include_trace=False) for run in runs],
    }


@lru_cache(maxsize=1)
def _builtin_public_leaderboard() -> dict[str, Any]:
    rows = [_public_row(policy) for policy in POLICIES.values()]
    ranked = _rank(rows, "public_score")
    for row in ranked:
        row["public_rank"] = row["public_score_rank"]
    provenance: dict[str, Any] = {
        "challenge_schema_version": CHALLENGE_VERSION,
        "exchange_version": "internal-clob-1.1",
        "scoring_version": SCORING_VERSION,
        "public_seed": PUBLIC_SEED,
        "seed_list": [PUBLIC_SEED],
        "world_variants": ["normal"],
        "code_commit": _code_commit(),
    }
    provenance["matrix_hash"] = sha256(
        json.dumps({"rows": ranked, "provenance": provenance}, sort_keys=True, default=str).encode()
    ).hexdigest()
    return {"challenge": challenge_overview(), "rows": ranked, "provenance": provenance}


def public_leaderboard_matrix(
    student_submissions: dict[str, ExecutionPolicySubmission] | None = None,
) -> dict[str, Any]:
    """Evaluate exact public seed 42; immutable built-ins are memoized."""
    matrix = deepcopy(_builtin_public_leaderboard())
    if not student_submissions:
        return matrix
    rows = matrix["rows"]
    for submission_id, submission in student_submissions.items():
        row = _public_row(policy_from_submission(submission, submission_id))
        row["submission_id"] = submission_id
        row["policy_version"] = submission.schema_version
        rows.append(row)
    matrix["rows"] = _rank(rows, "public_score")
    for row in matrix["rows"]:
        row["public_rank"] = row["public_score_rank"]
    matrix["provenance"]["student_submission_ids"] = sorted(student_submissions)
    matrix["provenance"].pop("matrix_hash", None)
    matrix["provenance"]["matrix_hash"] = sha256(
        json.dumps(
            {"rows": matrix["rows"], "provenance": matrix["provenance"]},
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    return matrix


def challenge_overview() -> dict[str, Any]:
    return {
        "challenge_id": CHALLENGE_ID,
        "title": "Trade the Shock",
        "phase": "public_practice",
        "objective": "Buy 6,000 shares of fictional NOVA inside one session while controlling implementation shortfall, participation, and terminal inventory.",
        "public_world": "Normal liquidity, stable background flow, low exchange latency, and no forced seller.",
        "hidden_worlds": {"count": len(HIDDEN_VARIANTS), "status": "withheld_until_release"},
        "policies": [policy.__dict__ for policy in POLICIES.values()],
        "quality": {
            "mechanical_validity": "PASS",
            "challenge_behavior": "VERIFIED_ON_PROTECTED_EVALUATION",
            "selected_synthetic_diagnostics": "REPORTED_NOT_CALIBRATED",
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
