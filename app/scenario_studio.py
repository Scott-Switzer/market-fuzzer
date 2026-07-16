"""Deterministic compilation of approved scenario packs into runnable worlds."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from app.schemas import WorldSpec
from app.world import build_demo_world, mutate_scenario

SCENARIO_TO_ENGINE_VARIANT = {
    "liquidity_withdrawal": "liquidity_withdrawal",
    "volatility_shock": "earnings_shock",
    "latency_shock": "latency_shock",
    "crowding": "crowded_unwind",
    "adverse_selection": "crowded_unwind",
    "completion_pressure": "crowded_unwind",
}


def compile_scenario_pack(manifest: dict[str, Any], *, seed: int = 42) -> dict[str, Any]:
    """Compile only allow-listed intents; never accept arbitrary world numbers."""
    base = build_demo_world(seed)
    compiled: list[dict[str, Any]] = []
    for index, intervention in enumerate(manifest["interventions"]):
        intervention_type = intervention["intervention_type"]
        variant = SCENARIO_TO_ENGINE_VARIANT[intervention_type]
        world, changes = mutate_scenario(base, variant)
        data = deepcopy(world.model_dump(mode="python"))
        data["world_id"] = f"compiled-{manifest['scenario_pack_id']}-{index}"
        for event in data["events"]:
            event["simulation_step"] = intervention["start_step"]
        world = WorldSpec.model_validate(data)
        world_json = world.model_dump(mode="json")
        compiled.append(
            {
                "world": world_json,
                "world_hash": hashlib.sha256(json.dumps(world_json, sort_keys=True).encode()).hexdigest(),
                "intent": intervention,
                "engine_mapping": changes,
            }
        )
    result = {
        "scenario_pack_id": manifest["scenario_pack_id"],
        "compiler": "deterministic_scenario_studio_v1",
        "seed": seed,
        "public_world": build_demo_world(seed).model_dump(mode="json"),
        "protected_worlds": compiled,
        "claim_boundary": "Compiled worlds are synthetic counterfactuals for declared stress-testing use.",
    }
    result["compile_hash"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result
