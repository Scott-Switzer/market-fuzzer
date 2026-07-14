from __future__ import annotations

import os
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import CompileResult, EventSpec, WorldSpec
from app.world import build_demo_world


class GPTCompilation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: WorldSpec
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """You compile natural-language counterfactual equity-market descriptions into the supplied
strict WorldSpec schema. Create exactly three fictional companies. Use safe bounded defaults, identify material
assumptions, and report warnings. Never emit code, tools, or shell commands. The deterministic simulator—not you—
will determine orders, prices, fills, and accounting. Reject impossible or contradictory requests by preserving a
safe valid specification and adding a clear warning."""


def compile_offline(prompt: str, seed: int = 42) -> CompileResult:
    if len(prompt) > 2_000:
        raise ValueError("prompt exceeds the 2,000-character offline compiler limit")
    text = prompt.lower().strip()
    if len(text) < 3:
        raise ValueError("prompt must contain at least three characters")
    if ("deep liquidity" in text or "very liquid" in text) and (
        "thin liquidity" in text or "shallow liquidity" in text
    ):
        raise ValueError("contradictory liquidity request: both deep and thin liquidity were specified")
    spec = build_demo_world(seed)
    data = deepcopy(spec.model_dump(mode="python"))
    assumptions = [
        "Three fictional U.S.-style equities trade on one continuous double-auction venue.",
        "Simulation time is compressed into 120 deterministic 30-second steps.",
    ]
    warnings = ["Offline rules were used; no model interpreted unstated economic intent."]
    mutations: list[str] = []
    if any(term in text for term in ("thin liquidity", "shallow liquidity", "liquidity withdrawal")):
        data["exchange"]["baseline_depth"] = 260
        data["events"].append(
            EventSpec(
                event_id="compiled-liquidity",
                simulation_step=45,
                scope="market",
                type="liquidity_withdrawal",
                liquidity_effect=0.35,
                narrative="Compiled liquidity-withdrawal event reduces displayed market-maker size.",
            ).model_dump()
        )
        mutations.append("thin liquidity")
    if any(term in text for term in ("earnings shock", "earnings miss", "negative earnings", "guidance cut")):
        mutations.append("negative earnings event")
    else:
        data["events"] = [event for event in data["events"] if event["type"] != "earnings"]
    if any(term in text for term in ("crowded momentum", "momentum unwind", "crowding")):
        for population in data["agents"]["populations"]:
            if population["type"] == "momentum":
                population["parameters"]["crowding"] = 2.4
        mutations.append("crowded momentum")
    if any(term in text for term in ("forced seller", "forced liquidation", "forced sale")):
        for population in data["agents"]["populations"]:
            if population["type"] == "forced_liquidator":
                population["parameters"].update({"start_step": 50, "total_quantity": 28_000})
        mutations.append("forced liquidation")
    if any(term in text for term in ("high latency", "latency shock", "slow network")):
        data["exchange"]["latency_profile"] = "high"
        mutations.append("high latency")
    if any(term in text for term in ("elevated volatility", "high volatility", "volatile", "crisis")):
        data["macro"]["volatility_regime"] = "crisis" if "crisis" in text else "elevated"
        mutations.append(f"{data['macro']['volatility_regime']} volatility")
    if "normal market" in text and not mutations:
        data["macro"]["volatility_regime"] = "normal"
        data["events"] = []
        assumptions.append("No scheduled stress event was requested.")
    data["world_id"] = "compiled-counterfactual-world"
    validated = WorldSpec.model_validate(data)
    assumptions.append("Compiled controls: " + (", ".join(mutations) if mutations else "normal baseline"))
    return CompileResult(spec=validated, compiler_mode="offline", assumptions=assumptions, warnings=warnings)


def compile_gpt(prompt: str, seed: int = 42, model: str | None = None) -> CompileResult:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set; use offline mode for the complete no-key demo")
    if len(prompt) > 2_000:
        raise ValueError("prompt exceeds the 2,000-character GPT compiler limit")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI SDK is not installed; install project dependencies or use offline mode"
        ) from exc
    selected_model: str = model or os.getenv("OPENAI_MODEL") or "gpt-5.6"
    client = OpenAI(timeout=30.0, max_retries=2)
    response = client.responses.parse(
        model=selected_model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Seed must be {seed}. Compile this world request:\n{prompt}"},
        ],
        text_format=GPTCompilation,
        max_output_tokens=8_000,
    )
    parsed = response.output_parsed
    if parsed is None:
        raise RuntimeError("GPT compiler returned no validated structured output")
    data = parsed.spec.model_dump()
    data["seed"] = seed
    spec = WorldSpec.model_validate(data)
    return CompileResult(
        spec=spec,
        compiler_mode="gpt",
        model=selected_model,
        assumptions=parsed.assumptions,
        warnings=parsed.warnings,
    )


def compile_world(prompt: str, seed: int = 42, mode: str = "offline") -> CompileResult:
    if mode == "offline":
        return compile_offline(prompt, seed)
    if mode == "gpt":
        return compile_gpt(prompt, seed)
    raise ValueError("compiler mode must be 'offline' or 'gpt'")
