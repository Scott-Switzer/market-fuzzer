"""Submission orchestrator: ties the locked strategy through the full pipeline.

compile -> approve -> hash -> historical backtest -> sealed synthetic stress ->
minimization -> adjacent pass -> evidence package.

The strategy hash is the invariant that binds every stage. We compute it from the
canonical DSL `Strategy` (reusing `dsl.ledger_hash` + `ApprovalService.lock`) so the
same hash appears in backtest, campaign, replay, and export.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.strategy_lab.dsl import Strategy
from app.strategy_lab.service_lab import ApprovalService
from app.strategy_lab.submission.engine import run_portfolio_backtest
from app.strategy_lab.submission.fenrix_adapter import load_panel as fenrix_load
from app.strategy_lab.submission.fixture import build_fixture_panel
from app.strategy_lab.submission.panels import DataProvenance, MarketDataPanel
from app.strategy_lab.submission.strategy import (
    DEMO_UNIVERSE,
    FIXED_END,
    FIXED_START,
    CrossSectionalSpec,
)
from app.strategy_lab.submission.stress_search import (
    DEFAULT_PREDICATES,
    FailurePredicate,
    run_fast_search,
)
from app.strategy_lab.submission.yfinance_adapter import acquire as yfinance_acquire


# ---------------------------------------------------------------------------
# Strategy identity (reuses the existing DSL/hash infrastructure)
# ---------------------------------------------------------------------------
def build_strategy_dsl(spec: CrossSectionalSpec) -> Strategy:
    """Construct a canonical Strategy from the flagship spec (every clause ledgered)."""
    from app.strategy_lab.dsl import (
        ClauseLedgerEntry,
        ClauseResolution,
        ClauseStatus,
        CostCap,
        ExecutionPolicy,
        FillTarget,
        Hold,
        OrderedClause,
        TimeInForce,
        ValueQualityLongShort,
    )

    ledger = []
    for frag in spec.to_clause_ledger_fragment():
        ledger.append(
            ClauseLedgerEntry(
                clause_id=frag["id"],
                original_text=json.dumps(frag),
                normalized_text=json.dumps(frag),
                status=ClauseStatus.SUPPORTED_AND_COMPILED,
                user_resolution=ClauseResolution.APPROVED,
                compiler_confidence=1.0,
            )
        )
    clauses = [
        OrderedClause(order=0, clause=Hold(note="pre_signal"), clause_id="c_0"),
        OrderedClause(
            order=1,
            clause=ValueQualityLongShort(
                long_top_n=int(round(spec.long_quantile * len(spec.universe))),
                short_bottom_n=int(round(spec.short_quantile * len(spec.universe))),
                beta_neutralize=False,
            ),
            clause_id="c_1",
        ),
        OrderedClause(
            order=2,
            clause=CostCap(max_bps=spec.commission_bps + spec.spread_bps + spec.slippage_bps),
            clause_id="c_2",
        ),
        OrderedClause(order=3, clause=Hold(note="exit_when_rebalanced"), clause_id="c_3"),
    ]
    strategy = Strategy(
        family="cross_sectional_momentum_volatility",
        name="Fenrix Flagship Long/Short Momentum-Volatility",
        description=(
            "Each month rank eligible equities by 12-1 momentum and trailing volatility; "
            "go long strong low-vol names and short weak high-vol names, equal weight, "
            "100% gross / ~0% net, max 10% position, trade at next open."
        ),
        description_original=(
            "Buy the strongest low-volatility names and short the weakest high-volatility names "
            "using 12-1 month momentum, rebalanced monthly at the next open."
        ),
        is_locked=False,
        execution_policy=ExecutionPolicy(
            fill_target=FillTarget.mid, max_order_qty=0, time_in_force=TimeInForce.day
        ),
        clauses=clauses,
        clause_ledger=ledger,
        universe={"type": "fixed_demo_universe", "assets": list(spec.universe), "benchmark": spec.benchmark},
        frequency={"signal": spec.signal_frequency, "rebalance": spec.rebalance_frequency},
        portfolio={
            "type": "cross_sectional_long_short",
            "long_quantile": spec.long_quantile,
            "short_quantile": spec.short_quantile,
            "gross_exposure": spec.gross_exposure,
            "net_exposure": spec.net_exposure,
            "weighting": spec.weighting,
            "max_position_weight": spec.max_position_weight,
        },
        execution={
            "decision_time": spec.decision_time,
            "fill_time": spec.fill_time,
            "commission_bps": spec.commission_bps,
            "spread_bps": spec.spread_bps,
            "slippage_bps": spec.slippage_bps,
            "borrow_bps": spec.borrow_bps,
        },
        benchmark={"symbol": spec.benchmark},
    )
    return strategy


def lock_strategy(strategy: Strategy, actor: str = "submission") -> dict[str, Any]:
    approval = ApprovalService.lock(strategy, actor=actor)
    return approval


# ---------------------------------------------------------------------------
# Data acquisition (three tiers)
# ---------------------------------------------------------------------------
def acquire_panel(
    *,
    mode: str = "auto",
    use_cache: bool = True,
    fenrix_path: str | None = None,
    universe: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Returns {'panel', 'mode', 'tier', 'quality', 'error'?}."""
    uni = universe or list(DEMO_UNIVERSE)
    st = start or FIXED_START
    en = end or FIXED_END

    if mode in ("auto", "fenrix"):
        fr = fenrix_load(explicit=fenrix_path, write_inventory=(mode == "fenrix"))
        if fr.get("panel") is not None:
            return {
                "panel": fr["panel"],
                "mode": "fenrix",
                "tier": 1,
                "quality": fr.get("inventory", {}),
                "fundamentals": fr.get("fundamentals"),
            }
        if mode == "fenrix":
            return {
                "panel": None,
                "mode": "fenrix",
                "tier": 1,
                "error": fr.get("error"),
                "quality": fr.get("inventory", {}),
            }
        # fall through to yfinance

    if mode in ("auto", "yfinance"):
        yf = yfinance_acquire(tickers=uni, start=st, end=en, use_cache=use_cache)
        if yf.get("panel") is not None:
            return {
                "panel": yf["panel"],
                "mode": "yfinance",
                "tier": 2,
                "quality": yf.get("quality", {}),
                "provenance": yf.get("provenance"),
            }
        if mode == "yfinance":
            return {
                "panel": None,
                "mode": "yfinance",
                "tier": 2,
                "error": yf.get("error"),
                "quality": yf.get("quality", {}),
            }
        # fall through to fixture

    # Tier 3: deterministic synthetic fixture (always available, labeled)
    panel = build_fixture_panel()
    return {
        "panel": panel,
        "mode": "synthetic_fixture",
        "tier": 3,
        "quality": {
            "status": "ok",
            "notice": "Deterministic synthetic fixture (Tier 3). Not historical data.",
        },
        "warning": "Falling back to synthetic fixture; historical data unavailable.",
    }


# ---------------------------------------------------------------------------
# Top-level run
# ---------------------------------------------------------------------------
@dataclass
class SubmissionRun:
    strategy_hash: str
    approval: dict[str, Any]
    panel_meta: dict[str, Any]
    data_mode: str
    backtest: dict[str, Any]
    stress: dict[str, Any]
    minimized: dict[str, Any] | None
    adjacent_pass: dict[str, Any] | None
    evidence: dict[str, Any] = field(default_factory=dict)


def run_submission(
    *,
    spec: CrossSectionalSpec | None = None,
    mode: str = "auto",
    use_cache: bool = True,
    fenrix_path: str | None = None,
    predicates: list[FailurePredicate] | None = None,
    budget: int = 24,
) -> SubmissionRun:
    spec = spec or CrossSectionalSpec()
    strategy = build_strategy_dsl(spec)
    approval = lock_strategy(strategy)
    strategy_hash = approval["strategy_id"]
    # hash invariant: the approval's canonical hash must equal the strategy's ledger hash
    assert approval["canonical_hash"] == strategy_hash, "strategy hash invariant broken at lock"

    # ---- historical backtest (Tier per mode) ----
    acquired = acquire_panel(
        mode=mode,
        use_cache=use_cache,
        fenrix_path=fenrix_path,
        universe=list(spec.universe),
        start=spec.start,
        end=spec.end,
    )
    panel = acquired["panel"]
    if panel is None:
        raise RuntimeError(f"Data acquisition failed: {acquired.get('error')}")
    bt = run_portfolio_backtest(panel=panel, spec=spec, strategy_hash=strategy_hash)
    backtest_payload = {
        "backtest_id": bt.backtest_id,
        "strategy_hash": bt.strategy_hash,
        "equity_curve": [round(float(x), 4) for x in bt.equity_curve],
        "metrics": bt.metrics,
        "cost_summary": bt.cost_summary,
        "trades": bt.trades[:50],
        "gross_exposure": [round(float(x), 4) for x in bt.gross_exposure],
        "net_exposure": [round(float(x), 4) for x in bt.net_exposure],
        "turnover": [round(float(x), 4) for x in bt.turnover],
        "target_weights": bt.target_weights.tolist(),
        "assets": bt.assets,
        "dates": bt.dates,
        "data_mode": acquired["mode"],
        "tier": acquired["tier"],
        "provenance": bt.provenance,
    }

    # ---- sealed synthetic stress (Tier A fast) ----
    stress = run_fast_search(
        strategy_hash=strategy_hash,
        spec=spec,
        base_assets=list(spec.universe)
        if mode == "synthetic_fixture"
        else ["SYN_A", "SYN_B", "SYN_C", "SYN_D", "SYN_E", "SYN_F", "SPY"],
        T=504,
        budget=budget,
        predicates=predicates or DEFAULT_PREDICATES,
    )

    # ---- minimization + adjacent pass on first failure (deterministic) ----
    minimized = None
    adjacent = None
    if stress["failures"]:
        f0 = stress["failures"][0]
        minimized = _minimize_failure(strategy_hash, spec, f0, stress["predicates"])
        adjacent = _adjacent_pass(strategy_hash, spec, f0)

    return SubmissionRun(
        strategy_hash=strategy_hash,
        approval=approval,
        panel_meta={"assets": bt.assets, "dates": bt.dates, "source": acquired["mode"]},
        data_mode=acquired["mode"],
        backtest=backtest_payload,
        stress=stress,
        minimized=minimized,
        adjacent_pass=adjacent,
    )


def _minimize_failure(strategy_hash, spec, failure, predicates) -> dict[str, Any]:
    """Delta-debug: shrink intensity until the predicate no longer fires (or floor)."""
    mech = failure["mechanism"]
    seed = failure["seed"]
    base_intensity = failure["intensity"]
    preds = [FailurePredicate(p["name"], p["kind"], p["threshold"]) for p in predicates]
    lo, hi = 0.0, base_intensity
    # binary search for minimal intensity that still fails
    for _ in range(8):
        mid = (lo + hi) / 2.0
        res = _eval_single(strategy_hash, spec, mech, mid, seed)
        if any(p.violated(res.metrics) for p in preds):
            hi = mid
        else:
            lo = mid
    minimized_intensity = hi
    res = _eval_single(strategy_hash, spec, mech, minimized_intensity, seed)
    return {
        "mechanism": mech,
        "original_intensity": base_intensity,
        "minimized_intensity": round(minimized_intensity, 4),
        "seed": seed,
        "metrics": res.metrics,
        "still_fails": any(p.violated(res.metrics) for p in preds),
        "predicates": [p.name for p in preds],
        "strategy_hash": strategy_hash,
    }


def _adjacent_pass(strategy_hash, spec, failure) -> dict[str, Any]:
    """Adjacent passing case: a nearby seed that does NOT violate predicates."""
    mech = failure["mechanism"]
    base_seed = failure["seed"]
    preds = DEFAULT_PREDICATES
    for delta in (1, -1, 2, -2, 3, -3, 5, -5):
        seed = base_seed + delta
        res = _eval_single(strategy_hash, spec, mech, failure["intensity"], seed)
        if not any(p.violated(res.metrics) for p in preds):
            return {
                "mechanism": mech,
                "seed": seed,
                "delta_from_failure_seed": delta,
                "metrics": res.metrics,
                "passes": True,
                "strategy_hash": strategy_hash,
            }
    return {
        "mechanism": mech,
        "note": "no adjacent pass found within search radius",
        "strategy_hash": strategy_hash,
    }


def _eval_single(strategy_hash, spec, mechanism, intensity, seed):
    import numpy as np

    from app.strategy_lab.submission.panels import AssetMetadata
    from app.strategy_lab.submission.stress_search import (
        _base_panel,
        _dummy_dates,
        _effective_spec,
        _fill_nan,
        apply_mechanism,
    )

    assets = ["SYN_A", "SYN_B", "SYN_C", "SYN_D", "SYN_E", "SYN_F", "SPY"]
    N = len(assets)
    T = 504
    base = _base_panel(assets, T, 12345, [100.0 + 20.0 * i for i in range(N)])
    stressed = _fill_nan(apply_mechanism(base, mechanism, intensity, seed))
    benchmark = stressed[:, assets.index("SPY")].copy()
    metadata = {a: AssetMetadata(ticker=a, is_benchmark=(a == "SPY")) for a in assets}
    prov = DataProvenance(source="deterministic_fixture", tier=3, label=f"min {mechanism}")
    panel = MarketDataPanel(
        dates=_dummy_dates(T),
        assets=tuple(assets),
        open=stressed.copy(),
        high=stressed.copy(),
        low=stressed.copy(),
        close=stressed,
        volume=np.ones((T, N)),
        benchmark_close=benchmark,
        metadata=metadata,
        provenance=prov,
    )
    eff = _effective_spec(
        spec,
        mechanism,
        intensity,
        dict(
            commission_bps=spec.commission_bps,
            spread_bps=spec.spread_bps,
            slippage_bps=spec.slippage_bps,
            borrow_bps=spec.borrow_bps,
        ),
    )
    return run_portfolio_backtest(panel=panel, spec=eff, strategy_hash=strategy_hash)
