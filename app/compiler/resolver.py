"""Resolution application for compiled strategies (reset brief item 27).

When a CompilationResult has required_user_resolutions (e.g. a missing universe
or fallback), the user supplies answers and this module produces a resolved
StrategySpec draft. Applying resolutions is explicit and validated -- no silent
defaulting.
"""

from __future__ import annotations

from dataclasses import replace

from app.compiler.result import CompilationResult
from app.domain.strategy_spec import StrategyType


def apply_resolutions(result: CompilationResult, resolutions: dict[str, object]) -> CompilationResult:
    """Return a new CompilationResult with resolutions applied to the draft.

    Supported resolution keys:
      * ``universe``: list[str] of tickers.
      * ``fallback``: str fallback ticker (tactical allocation).
    """
    spec = result.strategy_spec_draft
    updates: dict[str, object] = {}

    raw_uni = resolutions.get("universe")
    if raw_uni is not None:
        assert isinstance(raw_uni, (list, tuple)), "universe resolution must be a list of tickers"
        updates["universe"] = [str(x) for x in raw_uni]

    if "fallback" in resolutions and spec.strategy_type == StrategyType.TACTICAL_ALLOCATION:
        fb = str(resolutions["fallback"]).upper()
        rules = dict(spec.ranking_or_threshold_rules)
        rules["fallback"] = fb
        updates["ranking_or_threshold_rules"] = rules
        cur = updates.get("universe")
        uni: list[str] = list(cur) if isinstance(cur, list) else list(spec.universe)
        if fb not in uni:
            uni.append(fb)
        updates["universe"] = uni

    new_spec = spec.model_copy(update=updates) if updates else spec
    # revalidate by re-instantiating from a full dump (model_copy(update) is not validated)
    new_spec = type(spec).model_validate(new_spec.model_dump(mode="python"))

    # clear the now-satisfied resolutions
    remaining = [r for r in result.required_user_resolutions if not any(r.startswith(k) for k in resolutions)]
    return replace(
        result,
        strategy_spec_draft=new_spec,
        required_user_resolutions=remaining,
    )


__all__ = ["apply_resolutions"]
