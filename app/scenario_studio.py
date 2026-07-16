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


def compile_scenario_pack(
    manifest: dict[str, Any], *, base_world_manifest: dict[str, Any], seed: int | None = None
) -> dict[str, Any]:
    """Compile only allow-listed intents; never accept arbitrary world numbers."""
    selected_seed = int(base_world_manifest.get("seed", 42) if seed is None else seed)
    base = build_demo_world(selected_seed)
    compiled: list[dict[str, Any]] = []
    for index, intervention in enumerate(manifest["interventions"]):
        intervention_type = intervention["intervention_type"]
        variant = SCENARIO_TO_ENGINE_VARIANT[intervention_type]
        engine_variant = "normal" if variant == "latency_shock" else variant
        world, changes = mutate_scenario(base, engine_variant)
        data = deepcopy(world.model_dump(mode="python"))
        if variant == "latency_shock":
            data["exchange"]["latency_profile"] = "high"
            changes = {"scenario": intervention_type, "changed": ["exchange latency profile becomes high"]}
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
        "base_world_id": manifest["base_world_id"],
        "base_world_manifest_hash": base_world_manifest.get("manifest_hash"),
        "calibration_pack_id": base_world_manifest.get("manifest", {}).get("calibration_pack_id"),
        "calibration_checksum": base_world_manifest.get("manifest", {}).get("calibration_checksum"),
        "engine_profile": "demo_equities_v1",
        "seed": selected_seed,
        "public_world": base.model_dump(mode="json"),
        "protected_worlds": compiled,
        "claim_boundary": "Compiled worlds are synthetic counterfactuals for declared stress-testing use.",
    }
    result["compile_hash"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result
