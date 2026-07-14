from __future__ import annotations

import re

from .models import WorldSpec


KEYWORDS = {
    "liquidity_withdrawal": ("thin liquidity", "shallow liquidity", "liquidity withdrawal", "liquidity disappears"),
    "earnings_shock": ("earnings shock", "earnings miss", "negative earnings", "guidance cut"),
    "crowded_unwind": ("crowded momentum", "momentum unwind", "forced seller", "forced liquidation"),
}


def compile_prompt(prompt: str, seed: int = 42) -> WorldSpec:
    """Small deterministic prompt compiler; an LLM adapter can replace this boundary later."""
    text = prompt.lower()
    scenario = "normal"
    for candidate, phrases in KEYWORDS.items():
        if any(phrase in text for phrase in phrases):
            scenario = candidate
            break

    parent_order = 1_800
    match = re.search(r"(\d[\d,]*)\s*(?:shares?|units?)", text)
    if match:
        parent_order = int(match.group(1).replace(",", ""))

    return WorldSpec(
        name="Compiled synthetic market",
        seed=seed,
        scenario=scenario,  # type: ignore[arg-type]
        parent_order_shares=parent_order,
        metadata={"compiler": "deterministic-keyword-mvp", "source_prompt": prompt},
    )

