"""End-to-end execution pipeline: StrategySpec -> executor -> TargetPlan ->
generic accounting -> BacktestResult (reset brief Phase 2 items 13, 24).

This is the ONE production path. It re-verifies the strategy hash at the
execution boundary (defense against drift) and keeps all strategy-specific
feature math inside executors -- the accounting engine never sees a spec field.
"""

from __future__ import annotations

import numpy as np

from app.domain.strategy_spec import StrategySpec
from app.strategies.accounting import BacktestResult, run_accounting
from app.strategies.executors._base import context_from_panel, cost_model_from_spec
from app.strategies.registry import StrategyRegistry, default_registry


def run_strategy(
    spec: StrategySpec,
    panel: object,
    *,
    registry: StrategyRegistry | None = None,
    initial_capital: float = 1_000_000.0,
    expected_hash: str | None = None,
) -> BacktestResult:
    """Execute ``spec`` against ``panel`` and return a full BacktestResult.

    ``expected_hash`` (e.g. the approved strategy hash) is re-verified against the
    live spec content before execution -- a hard stop on hash drift.
    """
    reg = registry or default_registry
    # Import executors lazily to ensure the default registry is populated.
    if not reg.supported_types():
        import app.strategies.executors  # noqa: F401  (registers into default_registry)

    live_hash = spec.compute_hash()
    if expected_hash is not None and expected_hash != live_hash:
        raise ValueError(
            f"strategy hash drift at execution boundary: expected {expected_hash}, got {live_hash}"
        )

    executor = reg.get(spec.strategy_type)
    context = context_from_panel(panel)

    issues = [i for i in executor.validate_spec(spec, context) if i.severity == "error"]
    if issues:
        raise ValueError(
            "spec failed executor validation: " + "; ".join(f"{i.code}:{i.message}" for i in issues)
        )

    plan = executor.build_targets(spec, context)
    if plan.strategy_hash != live_hash:
        raise ValueError("executor produced a plan with a mismatched strategy hash")

    benchmark_close = getattr(panel, "benchmark_close", None)
    return run_accounting(
        plan=plan,
        open_=np.asarray(panel.open, dtype=float),  # type: ignore[attr-defined]
        close=np.asarray(panel.close, dtype=float),  # type: ignore[attr-defined]
        dates=[d.isoformat() if hasattr(d, "isoformat") else str(d) for d in panel.dates],  # type: ignore[attr-defined]
        assets=list(panel.assets),  # type: ignore[attr-defined]
        initial_capital=initial_capital,
        cost_model=cost_model_from_spec(spec),
        benchmark_close=(np.asarray(benchmark_close, dtype=float) if benchmark_close is not None else None),
    )


__all__ = ["run_strategy"]
